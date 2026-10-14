// The JSON API the installer UI calls. Long work (SearXNG, the install) runs as a Job the page polls.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Windows.Forms;

namespace ArcherSetup
{
    class Job
    {
        public string State = "idle";   // idle | running | done | failed
        public string Step = "";
        public double Progress;
        public string Error = "";
        public readonly List<string> Log = new List<string>();
        public Dictionary<string, object> Result = new Dictionary<string, object>();

        public void Say(string line)
        {
            if (string.IsNullOrWhiteSpace(line)) return;
            lock (Log)
            {
                Log.Add(line.TrimEnd());
                if (Log.Count > 400) Log.RemoveRange(0, Log.Count - 400);
            }
        }

        public Dictionary<string, object> Snapshot(int since)
        {
            lock (Log)
            {
                int from = Math.Max(0, Math.Min(since, Log.Count));
                return new Dictionary<string, object>
                {
                    { "state", State }, { "step", Step }, { "progress", Math.Round(Progress, 4) },
                    { "error", Error }, { "log", Log.GetRange(from, Log.Count - from) }, { "next", Log.Count },
                    { "result", Result },
                };
            }
        }
    }

    static class Jobs
    {
        static readonly Dictionary<string, Job> all = new Dictionary<string, Job>();

        public static Job Get(string name)
        {
            lock (all)
            {
                Job j;
                return all.TryGetValue(name, out j) ? j : null;
            }
        }

        public static bool AnyRunning()
        {
            lock (all)
            {
                foreach (var j in all.Values) if (j.State == "running") return true;
                return false;
            }
        }

        // Starts work once; a second call while it runs (a double click) just returns the running job.
        public static Job Start(string name, Action<Job> work)
        {
            Job job;
            lock (all)
            {
                if (all.TryGetValue(name, out job) && job.State == "running") return job;
                job = new Job { State = "running" };
                all[name] = job;
            }
            var t = new Thread(() =>
            {
                try
                {
                    work(job);
                    job.Progress = 1;
                    job.State = "done";
                }
                catch (Exception e)
                {
                    job.Error = e.Message;
                    job.Say("Error: " + e.Message);
                    job.State = "failed";
                }
            });
            t.IsBackground = false;  // an install in flight outlives a closed window
            t.Start();
            return job;
        }
    }

    static class Api
    {
        public static object Handle(Request req)
        {
            var body = req.Method == "POST" ? req.Json() : new Dictionary<string, object>();
            switch (req.Path)
            {
                case "/api/state":
                    return State();
                case "/api/ping":
                    return Ok();
                case "/api/check":
                    return Check();
                case "/api/docker/start":
                    return new Dictionary<string, object> { { "ok", Docker.StartDesktop() } };
                case "/api/searxng/setup":
                    Jobs.Start("searxng", Docker.Setup);
                    return Ok();
                case "/api/searxng/choice":
                    Choices.SetSearch(req.Str(body, "mode"), req.Str(body, "url"), req.Str(body, "container"));
                    return Ok();
                case "/api/install":
                    Installer.Autostart = req.Str(body, "autostart") != "False";
                    Jobs.Start("install", Installer.Run);
                    return Ok();
                case "/api/job":
                    {
                        string name, since;
                        req.Query.TryGetValue("name", out name);
                        req.Query.TryGetValue("since", out since);
                        var job = Jobs.Get(name ?? "");
                        int from;
                        int.TryParse(since, out from);
                        return job == null ? new Dictionary<string, object> { { "state", "idle" } } : job.Snapshot(from);
                    }
                case "/api/launch":
                    return new Dictionary<string, object> { { "ok", Installer.Launch() } };
                case "/api/onboarded":
                    return new Dictionary<string, object> { { "onboarded", Installer.Onboarded() } };
                case "/api/browser/settings":
                    return new Dictionary<string, object> { { "ok", Browsers.OpenSettings() } };
                case "/api/open":
                    return new Dictionary<string, object> { { "ok", Installer.OpenArcher(req.Str(body, "q")) } };
                case "/api/folder":
                    return new Dictionary<string, object> { { "dir", PickFolder() } };
                case "/api/dir":
                    {
                        string dir = req.Str(body, "dir");
                        if (dir.Length > 0 && !Jobs.AnyRunning() && Path.IsPathRooted(dir)) Program.InstallDir = Path.GetFullPath(dir);
                        return new Dictionary<string, object> { { "dir", Program.InstallDir } };
                    }
                case "/api/quit":
                    Program.Quit.Set();
                    return Ok();
            }
            throw new Exception("Unknown endpoint " + req.Path);
        }

        static Dictionary<string, object> Ok()
        {
            return new Dictionary<string, object> { { "ok", true } };
        }

        static object State()
        {
            var browser = Browsers.Default();
            return new Dictionary<string, object>
            {
                { "version", Program.Version },
                { "dir", Program.InstallDir },
                { "existing", File.Exists(Path.Combine(Program.InstallDir, "pyproject.toml")) },
                { "dry", Program.Dry },
                { "browser", browser },
                { "searchUrl", Choices.BaseUrl() + "/search?q=%s" },
                { "suggestUrl", Choices.BaseUrl() + "/suggest?q=%s" },
                { "baseUrl", Choices.BaseUrl() },
            };
        }

        // The system check. Docker decides whether the SearXNG step appears at all.
        static object Check()
        {
            var docker = Docker.Status();
            var result = new Dictionary<string, object>
            {
                { "windows", WindowsName() },
                { "arch", Installer.Arm64() ? "ARM64" : "x64" },
                { "freeGb", FreeGb() },
                { "online", Installer.Online() },
                { "docker", docker },
            };
            return result;
        }

        // Environment.OSVersion reports 6.2 to unmanifested apps; the registry has the real release.
        static string WindowsName()
        {
            try
            {
                using (var k = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion"))
                {
                    int build;
                    int.TryParse(k.GetValue("CurrentBuildNumber") as string, out build);
                    string release = k.GetValue("DisplayVersion") as string ?? "";
                    return (build >= 22000 ? "Windows 11" : "Windows 10") + (release.Length > 0 ? " " + release : "");
                }
            }
            catch { return "Windows"; }
        }

        static double FreeGb()
        {
            try
            {
                var root = Path.GetPathRoot(Program.InstallDir);
                return Math.Round(new DriveInfo(root).AvailableFreeSpace / 1e9, 1);
            }
            catch { return -1; }
        }

        // FolderBrowserDialog needs an STA thread with no other UI on it.
        static string PickFolder()
        {
            string chosen = "";
            var t = new Thread(() =>
            {
                using (var owner = new Form { TopMost = true, ShowInTaskbar = false, Opacity = 0, StartPosition = FormStartPosition.CenterScreen })
                using (var dlg = new FolderBrowserDialog { Description = "Choose where to install Archer", ShowNewFolderButton = true })
                {
                    owner.Show();
                    dlg.SelectedPath = Directory.Exists(Program.InstallDir) ? Program.InstallDir : Path.GetDirectoryName(Program.InstallDir);
                    if (dlg.ShowDialog(owner) == DialogResult.OK)
                    {
                        string p = dlg.SelectedPath;
                        // Picking a parent folder installs into an Archer folder inside it.
                        chosen = Path.GetFileName(p).Equals("Archer", StringComparison.OrdinalIgnoreCase) ? p : Path.Combine(p, "Archer");
                    }
                }
            });
            t.SetApartmentState(ApartmentState.STA);
            t.Start();
            t.Join();
            if (chosen.Length > 0 && !Jobs.AnyRunning()) Program.InstallDir = chosen;
            return Program.InstallDir;
        }
    }

    // What the user chose on the SearXNG step, written into Archer's .env during the install.
    static class Choices
    {
        public static string SearchMode = "";   // "" (untouched) | searxng | duckduckgo
        public static string SearxngUrl = "", Container = "";

        public static void SetSearch(string mode, string url, string container)
        {
            SearchMode = mode == "searxng" ? "searxng" : mode == "duckduckgo" ? "duckduckgo" : "";
            SearxngUrl = url ?? "";
            Container = container ?? "";
        }

        public static string BaseUrl()
        {
            var env = EnvFile.Read(Path.Combine(Program.InstallDir, ".env"));
            string port, host;
            if (!env.TryGetValue("ARCHER_PORT", out port) || port.Length == 0) port = "4747";
            if (!env.TryGetValue("ARCHER_PUBLIC_HOST", out host) || host.Length == 0) host = "archer.localhost";
            return "http://" + host + ":" + port;
        }
    }
}
