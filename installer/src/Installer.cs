// The install itself: unpack Archer, get uv, let uv fetch Python and packages, add shortcuts,
// register with Apps & features, then write the search choice into .env.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Management;
using System.Net;
using System.Text;
using System.Threading;
using Microsoft.Win32;

namespace ArcherSetup
{
    static class Installer
    {
        const string RepoUrl = "https://github.com/Jashith127/Archer-Search";
        static DateTime launchedAt = DateTime.MinValue;
        public static bool Autostart = true;  // Archer's own Run-key entry; it brings Docker and SearXNG up too

        static string Dir { get { return Program.InstallDir; } }
        static string Venv(string exe) { return Path.Combine(Dir, ".venv", "Scripts", exe); }

        public static bool Arm64()
        {
            string a = (Environment.GetEnvironmentVariable("PROCESSOR_ARCHITEW6432") ?? Environment.GetEnvironmentVariable("PROCESSOR_ARCHITECTURE") ?? "");
            return a.Equals("ARM64", StringComparison.OrdinalIgnoreCase);
        }

        public static bool Online()
        {
            if (Program.Dry) return true;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("https://pypi.org/simple/");
                req.Method = "HEAD";
                req.Timeout = 6000;
                using (req.GetResponse()) return true;
            }
            catch (WebException e) { return e.Response != null; }
            catch { return false; }
        }

        public static void Run(Job job)
        {
            if (Program.Dry) { DryRun(job); return; }

            job.Step = "files";
            job.Progress = 0.01;
            Directory.CreateDirectory(Dir);
            StopRunning(job);
            Unpack(job);
            job.Progress = 0.08;

            job.Step = "uv";
            string uv = FindUv(job);
            job.Progress = 0.18;

            job.Step = "packages";
            Sync(job, uv);
            job.Progress = 0.86;

            job.Step = "shortcuts";
            string ico = Path.Combine(Dir, "data", "archer.ico");
            Directory.CreateDirectory(Path.GetDirectoryName(ico));
            Shell.Stream(Venv("python.exe"), "-m archer.icon \"" + ico + "\"", Dir, job, 60000);
            if (Program.Integrate) Shortcuts(job, ico);
            if (Program.Integrate && Autostart)
            {
                Shell.Stream(Venv("python.exe"), "-c \"from archer import autostart; autostart.set_enabled(True)\"", Dir, job, 60000);
                job.Say("Archer will start when you sign in to Windows");
            }
            job.Progress = 0.94;

            job.Step = "finish";
            WriteChoices(job);
            if (Program.Integrate) Register(ico);
            job.Say("Archer " + Program.Version + " is installed in " + Dir);
            job.Result["dir"] = Dir;
        }

        // An upgrade replaces files that the running app holds open, so stop this install's Archer first.
        static void StopRunning(Job job)
        {
            string venv = Path.Combine(Dir, ".venv").ToLowerInvariant();
            try
            {
                using (var search = new ManagementObjectSearcher("SELECT ProcessId, ExecutablePath FROM Win32_Process"))
                    foreach (ManagementObject p in search.Get())
                    {
                        var path = p["ExecutablePath"] as string;
                        if (path == null || !path.ToLowerInvariant().StartsWith(venv)) continue;
                        job.Say("Stopping the running Archer (" + p["ProcessId"] + ")");
                        Shell.Capture("taskkill.exe", "/T /F /PID " + p["ProcessId"], 10000);
                    }
            }
            catch (Exception e) { job.Say("Couldn't check for a running Archer: " + e.Message); }
        }

        // Unpacks the app bundled in this exe. Old source goes first so deleted modules don't linger;
        // .env, data/ and the SearXNG folder are the user's and are never touched.
        static void Unpack(Job job)
        {
            string src = Path.Combine(Dir, "src");
            if (Directory.Exists(src) && File.Exists(Path.Combine(Dir, "pyproject.toml")))
            {
                job.Say("Replacing the previous version");
                Directory.Delete(src, true);
            }
            using (var stream = Resources.Open("payload.zip"))
            using (var zip = new ZipArchive(stream, ZipArchiveMode.Read))
            {
                int n = 0;
                foreach (var entry in zip.Entries)
                {
                    if (entry.FullName.EndsWith("/")) continue;
                    string target = Path.GetFullPath(Path.Combine(Dir, entry.FullName));
                    if (!target.StartsWith(Path.GetFullPath(Dir), StringComparison.OrdinalIgnoreCase)) continue;
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    entry.ExtractToFile(target, true);
                    n++;
                }
                job.Say("Unpacked " + n + " files into " + Dir);
            }
        }

        static string FindUv(Job job)
        {
            string bundled = Path.Combine(Dir, "bin", "uv.exe");
            if (File.Exists(bundled)) { job.Say("Using uv from " + bundled); return bundled; }
            string onPath = Shell.Which("uv.exe");
            if (onPath != null) { job.Say("Using uv from " + onPath); return onPath; }

            string asset = Arm64() ? "uv-aarch64-pc-windows-msvc.zip" : "uv-x86_64-pc-windows-msvc.zip";
            string url = "https://github.com/astral-sh/uv/releases/latest/download/" + asset;
            string zipPath = Path.Combine(Path.GetTempPath(), "archer-" + asset);
            job.Say("Downloading uv (" + asset + ")");
            using (var web = new WebClient())
            {
                web.Headers["User-Agent"] = "ArcherSetup";
                var done = new ManualResetEvent(false);
                Exception failed = null;
                web.DownloadProgressChanged += (s, e) =>
                {
                    if (e.TotalBytesToReceive > 0) job.Progress = 0.08 + 0.09 * e.BytesReceived / e.TotalBytesToReceive;
                };
                web.DownloadFileCompleted += (s, e) => { failed = e.Error; done.Set(); };
                web.DownloadFileAsync(new Uri(url), zipPath);
                done.WaitOne();
                if (failed != null) throw new Exception("Couldn't download uv: " + failed.Message);
            }
            Directory.CreateDirectory(Path.GetDirectoryName(bundled));
            using (var zip = ZipFile.OpenRead(zipPath))
                foreach (var entry in zip.Entries)
                    if (entry.Name == "uv.exe" || entry.Name == "uvw.exe" || entry.Name == "uvx.exe")
                        entry.ExtractToFile(Path.Combine(Dir, "bin", entry.Name), true);
            File.Delete(zipPath);
            job.Say("uv is ready");
            return bundled;
        }

        // uv picks a suitable Python (downloading one when needed) and installs Archer's locked packages.
        static void Sync(Job job, string uv)
        {
            job.Say("Installing Python and Archer's packages");
            double floor = 0.2;
            var started = DateTime.UtcNow;
            var ticker = new Timer(_ =>
            {
                // Ease toward the next milestone so the bar keeps moving through quiet stretches.
                double target = Math.Min(0.82, floor + 0.12);
                job.Progress = Math.Max(job.Progress, job.Progress + (target - job.Progress) * 0.06);
            }, null, 400, 400);
            try
            {
                var env = new Dictionary<string, string> { { "UV_NO_PROGRESS", "1" }, { "VIRTUAL_ENV", "" } };
                int code = Shell.Stream(uv, "sync --frozen --no-dev", Dir, job, 1200000, line =>
                {
                    string l = line.ToLowerInvariant();
                    if (l.Contains("cpython") || l.Contains("python")) floor = Math.Max(floor, 0.3);
                    if (l.Contains("creating virtual environment")) floor = Math.Max(floor, 0.4);
                    if (l.StartsWith("resolved")) floor = Math.Max(floor, 0.5);
                    if (l.StartsWith("prepared")) floor = Math.Max(floor, 0.7);
                    if (l.StartsWith("installed")) floor = Math.Max(floor, 0.82);
                    job.Progress = Math.Max(job.Progress, floor);
                }, env);
                if (code != 0) throw new Exception("uv couldn't install Archer's packages (exit " + code + "). The log above has details.");
            }
            finally { ticker.Dispose(); }
            if (!File.Exists(Venv("archerw.exe"))) throw new Exception("The install finished without Archer's launcher.");
            job.Say(string.Format("Packages ready in {0:0}s", (DateTime.UtcNow - started).TotalSeconds));
        }

        static void Shortcuts(Job job, string ico)
        {
            var shellType = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(shellType);
            foreach (var dir in new[] { Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), Environment.GetFolderPath(Environment.SpecialFolder.Programs) })
            {
                dynamic lnk = shell.CreateShortcut(Path.Combine(dir, "Archer.lnk"));
                lnk.TargetPath = Venv("archerw.exe");
                lnk.Arguments = "";
                lnk.WorkingDirectory = Dir;
                lnk.IconLocation = ico;
                lnk.Description = "Archer search engine";
                lnk.Save();
            }
            job.Say("Added Archer to the desktop and Start menu");
        }

        static void WriteChoices(Job job)
        {
            string env = Path.Combine(Dir, ".env");
            var values = new Dictionary<string, string>();
            if (Choices.SearchMode == "searxng")
            {
                values["ARCHER_SEARCH_PROVIDER"] = "searxng";
                values["ARCHER_SEARXNG_URL"] = Choices.SearxngUrl;
                values["ARCHER_RESULTS_URL"] = Choices.SearxngUrl.Replace("127.0.0.1", "localhost") + "/search?q={q}";
                if (Choices.Container.Length > 0) values["ARCHER_SEARXNG_CONTAINER"] = Choices.Container;
            }
            else if (Choices.SearchMode == "duckduckgo" && !EnvFile.Read(env).ContainsKey("ARCHER_SEARCH_PROVIDER"))
            {
                // Without SearXNG, start on DuckDuckGo; an upgrade keeps whatever was chosen before.
                values["ARCHER_SEARCH_PROVIDER"] = "duckduckgo";
            }
            if (values.Count == 0) return;
            EnvFile.Set(env, values);
            job.Say("Saved search settings to .env");
        }

        static void Register(string ico)
        {
            using (var key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Archer"))
            {
                string script = Path.Combine(Dir, "scripts", "uninstall.ps1");
                string cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\" -RemoveFiles";
                key.SetValue("DisplayName", "Archer");
                key.SetValue("DisplayVersion", Program.Version);
                key.SetValue("Publisher", "Archer");
                key.SetValue("DisplayIcon", ico);
                key.SetValue("InstallLocation", Dir);
                key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
                key.SetValue("URLInfoAbout", RepoUrl);
                key.SetValue("HelpLink", RepoUrl);
                key.SetValue("UninstallString", cmd);
                key.SetValue("QuietUninstallString", cmd + " -Quiet");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                key.SetValue("EstimatedSize", 120000, RegistryValueKind.DWord);  // KB, packages included
            }
        }

        public static bool Launch()
        {
            launchedAt = DateTime.UtcNow;
            if (Program.Dry) return true;
            var exe = Venv("archerw.exe");
            if (!File.Exists(exe)) return false;
            Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = Dir, UseShellExecute = false });
            return true;
        }

        // The companion app records a finished first-run setup in data/companion.json.
        public static bool Onboarded()
        {
            if (Program.Dry) return launchedAt != DateTime.MinValue && (DateTime.UtcNow - launchedAt).TotalSeconds > 6;
            try
            {
                var text = File.ReadAllText(Path.Combine(Dir, "data", "companion.json"));
                return System.Text.RegularExpressions.Regex.IsMatch(text, "\"onboarded\"\\s*:\\s*true");
            }
            catch { return false; }
        }

        public static bool OpenArcher(string q)
        {
            string url = Choices.BaseUrl() + "/";
            if (!string.IsNullOrWhiteSpace(q)) url += "search?q=" + Uri.EscapeDataString(q.Trim());
            if (Program.Dry) return true;
            try { Process.Start(url); return true; }
            catch { return false; }
        }

        static void DryRun(Job job)
        {
            string[][] plan = {
                new[] { "files", "Unpacked 142 files into " + Dir },
                new[] { "uv", "Downloading uv (uv-x86_64-pc-windows-msvc.zip)" },
                new[] { "packages", "Using CPython 3.12.11" },
                new[] { "packages", "Creating virtual environment at: .venv" },
                new[] { "packages", "Resolved 41 packages in 2ms" },
                new[] { "packages", "Prepared 39 packages in 4.18s" },
                new[] { "packages", "Installed 39 packages in 812ms" },
                new[] { "shortcuts", "Added Archer to the desktop and Start menu" },
                new[] { "finish", "Archer " + Program.Version + " is installed in " + Dir },
            };
            for (int i = 0; i < plan.Length; i++)
            {
                job.Step = plan[i][0];
                job.Say(plan[i][1]);
                for (int k = 0; k < 10; k++) { Thread.Sleep(90); job.Progress = (i + k / 10.0) / plan.Length; }
            }
            job.Result["dir"] = Dir;
        }
    }
}
