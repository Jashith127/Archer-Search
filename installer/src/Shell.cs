// Process, .env and browser helpers.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace ArcherSetup
{
    class Captured
    {
        public int Code;
        public string Output = "";
    }

    static class Shell
    {
        public static string Which(string exe)
        {
            foreach (var dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
            {
                try
                {
                    if (dir.Trim().Length == 0) continue;
                    var p = Path.Combine(dir.Trim().Trim('"'), exe);
                    if (File.Exists(p)) return p;
                }
                catch (ArgumentException) { }
            }
            return null;
        }

        static ProcessStartInfo Info(string exe, string args, string cwd)
        {
            var psi = new ProcessStartInfo(exe, args)
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
            };
            if (cwd != null) psi.WorkingDirectory = cwd;
            return psi;
        }

        public static Captured Capture(string exe, string args, int timeoutMs)
        {
            var result = new Captured { Code = -1 };
            try
            {
                using (var p = Process.Start(Info(exe, args, null)))
                {
                    var err = p.StandardError.ReadToEndAsync();
                    var output = p.StandardOutput.ReadToEndAsync();
                    if (!p.WaitForExit(timeoutMs)) { try { p.Kill(); } catch { } return result; }
                    result.Output = output.Result + err.Result;
                    result.Code = p.ExitCode;
                }
            }
            catch { }
            return result;
        }

        // Runs a command, sending each output line to the job's log (and to onLine). Returns the exit code.
        public static int Stream(string exe, string args, string cwd, Job job, int timeoutMs, Action<string> onLine = null,
                                 Dictionary<string, string> env = null)
        {
            var psi = Info(exe, args, cwd);
            if (env != null) foreach (var kv in env) psi.EnvironmentVariables[kv.Key] = kv.Value;
            using (var p = new Process { StartInfo = psi })
            {
                DataReceivedEventHandler handler = (s, e) =>
                {
                    if (e.Data == null) return;
                    // uv redraws progress with carriage returns; keep the last frame.
                    string line = e.Data;
                    int cr = line.LastIndexOf('\r');
                    if (cr >= 0) line = line.Substring(cr + 1);
                    line = Regex.Replace(line, @"\x1b\[[0-9;?]*[A-Za-z]", "");
                    job.Say(line);
                    if (onLine != null) onLine(line);
                };
                p.OutputDataReceived += handler;
                p.ErrorDataReceived += handler;
                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                if (!p.WaitForExit(timeoutMs))
                {
                    try { p.Kill(); } catch { }
                    throw new Exception(Path.GetFileNameWithoutExtension(exe) + " took too long and was stopped.");
                }
                p.WaitForExit();
                return p.ExitCode;
            }
        }
    }

    // Archer's .env: KEY=value lines with comments. Only active lines are read; updates keep everything else.
    static class EnvFile
    {
        static readonly Regex Line = new Regex(@"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$");

        public static Dictionary<string, string> Read(string path)
        {
            var d = new Dictionary<string, string>();
            if (!File.Exists(path)) return d;
            foreach (var raw in File.ReadAllLines(path))
            {
                var m = Line.Match(raw);
                if (!m.Success) continue;
                string v = m.Groups[2].Value.Trim();
                if (v.Length >= 2 && (v[0] == '"' || v[0] == '\'') && v[v.Length - 1] == v[0]) v = v.Substring(1, v.Length - 2);
                d[m.Groups[1].Value] = v;
            }
            return d;
        }

        public static void Set(string path, Dictionary<string, string> values)
        {
            var lines = File.Exists(path)
                ? new List<string>(File.ReadAllLines(path))
                : new List<string> { "# Archer settings. Archer's Settings window edits this file and keeps its comments." };
            var pending = new Dictionary<string, string>(values);
            for (int i = 0; i < lines.Count; i++)
            {
                var m = Line.Match(lines[i]);
                if (m.Success && pending.ContainsKey(m.Groups[1].Value))
                {
                    lines[i] = m.Groups[1].Value + "=" + pending[m.Groups[1].Value];
                    pending.Remove(m.Groups[1].Value);
                }
            }
            foreach (var kv in values) if (pending.ContainsKey(kv.Key)) lines.Add(kv.Key + "=" + kv.Value);
            File.WriteAllText(path, string.Join("\r\n", lines) + "\r\n", new UTF8Encoding(false));
        }
    }

    static class Browsers
    {
        // ProgId prefix -> (id, name, the page that lists search engines)
        static readonly string[][] Known =
        {
            new[] { "Helium", "helium", "Helium", "chrome://settings/searchEngines" },
            new[] { "ChromeHTML", "chrome", "Google Chrome", "chrome://settings/searchEngines" },
            new[] { "MSEdgeHTM", "edge", "Microsoft Edge", "edge://settings/searchEngines" },
            new[] { "BraveHTML", "brave", "Brave", "brave://settings/searchEngines" },
            new[] { "VivaldiHTM", "vivaldi", "Vivaldi", "vivaldi://settings/search" },
            new[] { "Opera", "opera", "Opera", "opera://settings/searchEngines" },
            new[] { "FirefoxURL", "firefox", "Firefox", "about:preferences#search" },
        };

        static string ProgId()
        {
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"))
                    return key == null ? "" : (key.GetValue("ProgId") as string ?? "");
            }
            catch { return ""; }
        }

        static string ExeFor(string progId)
        {
            try
            {
                using (var key = Registry.ClassesRoot.OpenSubKey(progId + @"\shell\open\command"))
                {
                    var cmd = key == null ? null : key.GetValue("") as string;
                    if (string.IsNullOrEmpty(cmd)) return null;
                    var m = Regex.Match(cmd, "^\\s*\"([^\"]+)\"|^\\s*(\\S+)");
                    var exe = m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value;
                    return File.Exists(exe) ? exe : null;
                }
            }
            catch { return null; }
        }

        public static Dictionary<string, object> Default()
        {
            string progId = ProgId();
            foreach (var k in Known)
            {
                if (!progId.StartsWith(k[0], StringComparison.OrdinalIgnoreCase)) continue;
                return new Dictionary<string, object>
                {
                    { "id", k[1] }, { "name", k[2] }, { "settings", k[3] },
                    { "canOpen", ExeFor(progId) != null }, { "firefox", k[1] == "firefox" },
                };
            }
            return new Dictionary<string, object> { { "id", "other" }, { "name", "your browser" }, { "settings", "" }, { "canOpen", false }, { "firefox", false } };
        }

        public static bool OpenSettings()
        {
            if (Program.Dry) return true;
            string progId = ProgId();
            foreach (var k in Known)
            {
                if (!progId.StartsWith(k[0], StringComparison.OrdinalIgnoreCase)) continue;
                var exe = ExeFor(progId);
                if (exe == null) return false;
                try { Process.Start(new ProcessStartInfo(exe, k[3]) { UseShellExecute = false }); return true; }
                catch { return false; }
            }
            return false;
        }
    }
}
