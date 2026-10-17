// Removing Archer, from the same window that installs it (Apps & features runs this exe with
// --uninstall). Stops Archer and its SearXNG, removes the shortcuts, the start-with-Windows entry,
// SearXNG and the Apps & features entry, then the folder. Settings and history stay unless asked.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Management;
using System.Threading;
using Microsoft.Win32;

namespace ArcherSetup
{
    static class Uninstaller
    {
        public static bool DeleteData;  // .env (settings) and data\ (history, models); kept by default

        static string Dir { get { return Program.InstallDir; } }

        static string SearxngRoot
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Archer", "searxng"); }
        }

        // Only a folder that is plainly an Archer install is ever deleted.
        public static bool IsArcher(string dir)
        {
            try
            {
                string full = Path.GetFullPath(dir).TrimEnd('\\');
                if (full.Length <= 3 || full.Equals(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), StringComparison.OrdinalIgnoreCase)) return false;
                string toml = Path.Combine(full, "pyproject.toml");
                return File.Exists(toml) && File.ReadAllText(toml).Contains("name = \"archer\"")
                    && Directory.Exists(Path.Combine(full, "src", "archer"));
            }
            catch { return false; }
        }

        public static void Run(Job job)
        {
            if (Program.Dry) { DryRun(job); return; }
            if (!IsArcher(Dir)) throw new Exception("There's no Archer installed in " + Dir + ".");

            job.Step = "stop";
            job.Progress = 0.05;
            Stop(job);

            job.Step = "shortcuts";
            job.Progress = 0.25;
            Shortcuts(job);

            job.Step = "searxng";
            job.Progress = 0.45;
            if (Directory.Exists(SearxngRoot))
            {
                Delete(SearxngRoot);
                job.Say("Removed SearXNG from " + SearxngRoot);
            }

            job.Step = "files";
            job.Progress = 0.65;
            Thread.Sleep(500);  // let stopped processes release their files
            Files(job);
            using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Archer"))
            {
                var at = key == null ? null : key.GetValue("InstallLocation") as string;
                if (at != null && at.TrimEnd('\\').Equals(Dir.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                {
                    key.Close();
                    Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Archer", false);
                    job.Say("Removed Archer from Installed apps");
                }
            }
            job.Say("Archer is removed");
        }

        // Archer's own Python and SearXNG's (a Granian tree under its venv).
        static void Stop(Job job)
        {
            var roots = new[] { Path.Combine(Dir, ".venv").ToLowerInvariant(), SearxngRoot.ToLowerInvariant() };
            try
            {
                using (var search = new ManagementObjectSearcher("SELECT ProcessId, ExecutablePath FROM Win32_Process"))
                    foreach (ManagementObject p in search.Get())
                    {
                        var path = (p["ExecutablePath"] as string ?? "").ToLowerInvariant();
                        if (path.Length == 0 || Array.FindIndex(roots, r => path.StartsWith(r)) < 0) continue;
                        job.Say("Stopping " + Path.GetFileName(path) + " (" + p["ProcessId"] + ")");
                        Shell.Capture("taskkill.exe", "/T /F /PID " + p["ProcessId"], 10000);
                    }
            }
            catch (Exception e) { job.Say("Couldn't check for a running Archer: " + e.Message); }
        }

        static bool Mine(string path)
        {
            return !string.IsNullOrEmpty(path) && path.Trim('"').StartsWith(Dir, StringComparison.OrdinalIgnoreCase);
        }

        // Only this copy's shortcuts and autostart entry: a development checkout keeps its own.
        static void Shortcuts(Job job)
        {
            var shellType = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(shellType);
            foreach (var dir in new[] { Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), Environment.GetFolderPath(Environment.SpecialFolder.Programs) })
            {
                string lnk = Path.Combine(dir, "Archer.lnk");
                if (File.Exists(lnk) && Mine((string)shell.CreateShortcut(lnk).TargetPath)) File.Delete(lnk);
            }
            job.Say("Removed the desktop and Start menu shortcuts");
            using (var run = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
            {
                if (run != null && Mine(run.GetValue("Archer") as string))
                {
                    run.DeleteValue("Archer", false);
                    job.Say("Archer no longer starts with Windows");
                }
            }
        }

        static void Files(Job job)
        {
            if (DeleteData)
            {
                Delete(Dir);
                job.Say("Removed " + Dir);
                return;
            }
            var keep = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { ".env", "data" };
            foreach (var entry in Directory.GetFileSystemEntries(Dir))
            {
                if (keep.Contains(Path.GetFileName(entry))) continue;
                if (Directory.Exists(entry)) Delete(entry); else TryDelete(entry);
            }
            job.Say("Removed Archer; your settings and history stay in " + Dir);
        }

        static void Delete(string dir)
        {
            for (int attempt = 0; attempt < 3; attempt++)
            {
                try { Directory.Delete(dir, true); return; }
                catch (DirectoryNotFoundException) { return; }
                catch (IOException) { Thread.Sleep(700); }
                catch (UnauthorizedAccessException) { Thread.Sleep(700); }
            }
            if (Directory.Exists(dir)) throw new Exception("Couldn't remove " + dir + ". Close anything using it and try again.");
        }

        static void TryDelete(string file)
        {
            try { File.Delete(file); } catch { }
        }

        static void DryRun(Job job)
        {
            string[][] plan = {
                new[] { "stop", "Stopping archerw.exe (4120)" },
                new[] { "stop", "Stopping python.exe (5532)" },
                new[] { "shortcuts", "Removed the desktop and Start menu shortcuts" },
                new[] { "searxng", "Removed SearXNG from " + SearxngRoot },
                new[] { "files", DeleteData ? "Removed " + Dir : "Removed Archer; your settings and history stay in " + Dir },
            };
            for (int i = 0; i < plan.Length; i++)
            {
                job.Step = plan[i][0];
                job.Say(plan[i][1]);
                for (int k = 0; k < 10; k++) { Thread.Sleep(80); job.Progress = (i + k / 10.0) / plan.Length; }
            }
        }
    }
}
