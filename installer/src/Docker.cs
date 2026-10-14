// Docker and SearXNG: find Docker, notice a SearXNG that's already serving JSON, or run one for Archer.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace ArcherSetup
{
    static class Docker
    {
        public const string Container = "archer-searxng";
        const string Image = "searxng/searxng:latest";

        static string Local { get { return Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData); } }
        static string ProgramFiles { get { return Environment.GetEnvironmentVariable("ProgramFiles") ?? @"C:\Program Files"; } }

        public static string Cli()
        {
            string onPath = Shell.Which("docker.exe");
            if (onPath != null) return onPath;
            foreach (var p in new[] {
                Path.Combine(ProgramFiles, @"Docker\Docker\resources\bin\docker.exe"),
                Path.Combine(Local, @"Programs\DockerDesktop\resources\bin\docker.exe") })
                if (File.Exists(p)) return p;
            return null;
        }

        static string Desktop()
        {
            foreach (var p in new[] {
                Path.Combine(ProgramFiles, @"Docker\Docker\Docker Desktop.exe"),
                Path.Combine(Local, @"Programs\DockerDesktop\Docker Desktop.exe") })
                if (File.Exists(p)) return p;
            return null;
        }

        static string Run(string args, int timeoutMs)
        {
            var cli = Cli();
            if (cli == null) return null;
            var r = Shell.Capture(cli, args, timeoutMs);
            return r.Code == 0 ? r.Output.Trim() : null;
        }

        public static Dictionary<string, object> Status()
        {
            if (Program.Dry) return DryStatus();
            string cli = Cli();
            var d = new Dictionary<string, object>
            {
                { "installed", cli != null || Desktop() != null },
                { "canStart", Desktop() != null },
                { "running", false },
            };
            bool daemon = cli != null && Run("info --format {{.ServerVersion}}", 15000) != null;
            d["running"] = daemon;
            d["searxng"] = FindSearxng(daemon);
            return d;
        }

        static Dictionary<string, object> DryStatus()
        {
            var sx = new Dictionary<string, object> { { "state", "missing" } };
            return new Dictionary<string, object> { { "installed", true }, { "canStart", true }, { "running", true }, { "searxng", sx } };
        }

        // state: ready (serving JSON) | nojson (running, JSON output off) | stopped (our container exists) | missing
        static Dictionary<string, object> FindSearxng(bool daemon)
        {
            var urls = new List<string>();
            string configured;
            var env = EnvFile.Read(Path.Combine(Program.InstallDir, ".env"));
            if (env.TryGetValue("ARCHER_SEARXNG_URL", out configured) && configured.Length > 0) urls.Add(configured.TrimEnd('/'));
            urls.Add("http://127.0.0.1:8888");

            var containers = new Dictionary<string, string>();  // url -> container name
            if (daemon)
            {
                var ps = Run("ps --format \"{{.Names}}|{{.Image}}|{{.Ports}}\"", 15000) ?? "";
                foreach (var line in ps.Split('\n'))
                {
                    var parts = line.Trim().Split('|');
                    if (parts.Length < 3 || parts[1].IndexOf("searxng", StringComparison.OrdinalIgnoreCase) < 0) continue;
                    var m = Regex.Match(parts[2], @":(\d+)->8080/tcp");
                    if (!m.Success) continue;
                    string url = "http://127.0.0.1:" + m.Groups[1].Value;
                    containers[url] = parts[0];
                    if (!urls.Contains(url)) urls.Add(url);
                }
            }

            Dictionary<string, object> noJson = null;
            foreach (var url in urls)
            {
                string probe = Probe(url);
                string name;
                containers.TryGetValue(url, out name);
                var found = new Dictionary<string, object> { { "url", url }, { "container", name ?? "" } };
                if (probe == "json") { found["state"] = "ready"; return found; }
                if (probe == "html" && noJson == null) { found["state"] = "nojson"; noJson = found; }
            }

            if (daemon)
            {
                var all = Run("ps -a --filter name=^/" + Container + "$ --format {{.Names}}", 15000);
                if (!string.IsNullOrEmpty(all))
                    return new Dictionary<string, object> { { "state", "stopped" }, { "container", Container }, { "url", "" } };
            }
            return noJson ?? new Dictionary<string, object> { { "state", "missing" } };
        }

        // "json" when SearXNG answers searches in JSON (what Archer needs), "html" when something answers
        // but refuses JSON, null when nothing is there.
        public static string Probe(string url)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url + "/search?q=archer&format=json");
                req.Timeout = 8000;
                req.UserAgent = "ArcherSetup";
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var reader = new StreamReader(resp.GetResponseStream()))
                {
                    var text = reader.ReadToEnd().TrimStart();
                    return text.StartsWith("{") && text.Contains("\"results\"") ? "json" : "html";
                }
            }
            catch (WebException e)
            {
                return e.Response != null ? "html" : null;
            }
            catch { return null; }
        }

        public static bool StartDesktop()
        {
            if (Program.Dry) return true;
            var exe = Desktop();
            if (exe == null) return false;
            try { System.Diagnostics.Process.Start(exe); return true; }
            catch { return false; }
        }

        static int FreePort()
        {
            for (int port = 8888; port < 8900; port++)
            {
                try
                {
                    var l = new TcpListener(IPAddress.Loopback, port);
                    l.Start();
                    l.Stop();
                    return port;
                }
                catch (SocketException) { }
            }
            throw new Exception("No free port between 8888 and 8899 for SearXNG.");
        }

        public static void Setup(Job job)
        {
            if (Program.Dry) { DrySetup(job); return; }
            string cli = Cli();
            if (cli == null) throw new Exception("Docker isn't installed.");
            if (Run("info --format {{.ServerVersion}}", 20000) == null) throw new Exception("Docker isn't running. Start Docker Desktop and try again.");

            string url;
            string exists = Run("ps -a --filter name=^/" + Container + "$ --format {{.Names}}", 15000);
            if (!string.IsNullOrEmpty(exists))
            {
                job.Step = "start";
                job.Progress = 0.6;
                job.Say("Starting the existing " + Container + " container");
                if (Shell.Stream(cli, "start " + Container, null, job, 120000) != 0) throw new Exception("Docker couldn't start " + Container + ".");
                var port = Run("port " + Container + " 8080/tcp", 15000) ?? "";
                var m = Regex.Match(port, @":(\d+)");
                url = "http://127.0.0.1:" + (m.Success ? m.Groups[1].Value : "8888");
            }
            else
            {
                job.Step = "pull";
                job.Say("Downloading the SearXNG image");
                int layers = 0, done = 0;
                int code = Shell.Stream(cli, "pull " + Image, null, job, 900000, line =>
                {
                    if (line.Contains("Pulling fs layer")) layers++;
                    if (line.Contains("Pull complete") || line.Contains("Already exists")) done++;
                    if (layers > 0) job.Progress = Math.Min(0.6, 0.05 + 0.55 * done / layers);
                });
                if (code != 0) throw new Exception("Docker couldn't download SearXNG. Check your connection and try again.");

                job.Step = "config";
                job.Progress = 0.62;
                string dir = Path.Combine(Program.InstallDir, "searxng");
                Directory.CreateDirectory(dir);
                string settingsFile = Path.Combine(dir, "settings.yml");
                if (!File.Exists(settingsFile))
                {
                    File.WriteAllText(settingsFile, SettingsYaml(), new UTF8Encoding(false));
                    job.Say("Wrote " + settingsFile);
                }

                job.Step = "start";
                job.Progress = 0.68;
                int portNo = FreePort();
                url = "http://127.0.0.1:" + portNo;
                job.Say("Starting SearXNG on " + url);
                string args = string.Format(
                    "run -d --name {0} --restart unless-stopped -p 127.0.0.1:{1}:8080 -v \"{2}:/etc/searxng\" -e SEARXNG_BASE_URL=http://localhost:{1}/ {3}",
                    Container, portNo, dir, Image);
                if (Shell.Stream(cli, args, null, job, 120000) != 0) throw new Exception("Docker couldn't start SearXNG.");
            }

            job.Step = "wait";
            job.Say("Waiting for SearXNG to answer");
            var until = DateTime.UtcNow.AddSeconds(120);
            while (true)
            {
                if (Probe(url) == "json") break;
                if (DateTime.UtcNow > until) throw new Exception("SearXNG started but isn't answering yet. Try again in a minute.");
                job.Progress = Math.Min(0.97, job.Progress + 0.02);
                Thread.Sleep(2000);
            }
            job.Say("SearXNG is ready at " + url);
            job.Result["url"] = url;
            job.Result["container"] = Container;
            Choices.SetSearch("searxng", url, Container);
        }

        // SearXNG refuses to start with its default secret, and Archer reads results as JSON.
        static string SettingsYaml()
        {
            var key = new byte[32];
            using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(key);
            return "# SearXNG for Archer, created by Archer Setup. Docs: https://docs.searxng.org/admin/settings/\n" +
                   "use_default_settings: true\n" +
                   "server:\n" +
                   "  secret_key: \"" + BitConverter.ToString(key).Replace("-", "").ToLowerInvariant() + "\"\n" +
                   "  limiter: false\n" +
                   "  image_proxy: false\n" +
                   "search:\n" +
                   "  autocomplete: \"duckduckgo\"\n" +
                   "  formats:\n" +
                   "    - html\n" +
                   "    - json\n";
        }

        static void DrySetup(Job job)
        {
            string[][] steps = {
                new[] { "pull", "Downloading the SearXNG image" }, new[] { "config", "Writing settings.yml" },
                new[] { "start", "Starting SearXNG on http://127.0.0.1:8888" }, new[] { "wait", "Waiting for SearXNG to answer" } };
            for (int i = 0; i < steps.Length; i++)
            {
                job.Step = steps[i][0];
                job.Say(steps[i][1]);
                for (int k = 0; k < 12; k++)
                {
                    Thread.Sleep(140);
                    job.Progress = (i + k / 12.0) / steps.Length;
                    if (i == 0 && k % 3 == 0) job.Say("a1b2c3d4e5f" + k + ": Pull complete");
                }
            }
            job.Result["url"] = "http://127.0.0.1:8888";
            job.Result["container"] = Container;
            Choices.SetSearch("searxng", "http://127.0.0.1:8888", Container);
        }
    }
}
