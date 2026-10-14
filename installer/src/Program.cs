// ArcherSetup: a branded installer for Archer.
//
// The UI is plain HTML/CSS/JS (installer/ui) served from this process on a random loopback port and
// shown in a Chromium --app window, the same way Archer's companion app opens. Everything that
// touches the machine (Docker, downloads, uv, shortcuts, the registry) happens here, behind a
// per-run token. Builds with the .NET Framework compiler that ships with Windows (see build.ps1);
// the code therefore sticks to C# 5.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Archer Setup")]
[assembly: System.Reflection.AssemblyProduct("Archer")]
[assembly: System.Reflection.AssemblyDescription("Installs Archer, a personal search engine")]

namespace ArcherSetup
{
    static class Program
    {
        public static string Version = "0.0.0";
        public static string InstallDir;
        public static string UiDir;
        public static bool Dry;          // simulate every step; for working on the UI
        public static bool Integrate = true;  // shortcuts and Apps & features entry
        public static readonly ManualResetEvent Quit = new ManualResetEvent(false);
        public static DateTime LastPing = DateTime.UtcNow;
        static Process window;

        [STAThread]
        static int Main(string[] args)
        {
            System.Net.ServicePointManager.SecurityProtocol = (System.Net.SecurityProtocolType)3072; // TLS 1.2
            Version = Resources.Text("version.txt").Trim();
            InstallDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Archer");

            int devPort = 0;
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (a == "--dry") Dry = true;
                else if (a == "--no-integrate") Integrate = false;
                else if (a == "--dir" && i + 1 < args.Length) InstallDir = Path.GetFullPath(args[++i]);
                else if (a == "--ui" && i + 1 < args.Length) UiDir = Path.GetFullPath(args[++i]);
                else if (a == "--dev-port" && i + 1 < args.Length) devPort = int.Parse(args[++i]);
            }

            Server server;
            try { server = new Server(devPort); }
            catch (Exception e)
            {
                MessageBox.Show("Archer Setup could not start: " + e.Message, "Archer Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            server.Start();
            string url = "http://127.0.0.1:" + server.Port + "/";

            if (devPort != 0)
            {
                Console.WriteLine(url);
                Quit.WaitOne();
                return 0;
            }

            window = OpenWindow(url);
            // Close when the window goes away: either its process exits, or (when the browser keeps a
            // shared process alive) the page stops sending heartbeats. A running install finishes first.
            while (!Quit.WaitOne(1000))
            {
                bool gone = window != null && SafeExited(window);
                bool silent = (DateTime.UtcNow - LastPing).TotalSeconds > 20;
                if ((gone || silent) && !Jobs.AnyRunning()) break;
            }
            if (window != null && !SafeExited(window))
            {
                try { window.Kill(); } catch { }
            }
            return 0;
        }

        static bool SafeExited(Process p)
        {
            try { return p.HasExited; } catch { return true; }
        }

        // Edge first (every Windows 11 machine has it), then Chrome, Brave and Helium; a throwaway profile
        // keeps the window separate from the user's own browsing and lets us see when it closes.
        static Process OpenWindow(string url)
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string pf = Environment.GetEnvironmentVariable("ProgramFiles") ?? "";
            string pf86 = Environment.GetEnvironmentVariable("ProgramFiles(x86)") ?? "";
            var candidates = new List<string>
            {
                Path.Combine(pf86, @"Microsoft\Edge\Application\msedge.exe"),
                Path.Combine(pf, @"Microsoft\Edge\Application\msedge.exe"),
                Path.Combine(pf, @"Google\Chrome\Application\chrome.exe"),
                Path.Combine(local, @"Google\Chrome\Application\chrome.exe"),
                Path.Combine(pf, @"BraveSoftware\Brave-Browser\Application\brave.exe"),
                Path.Combine(local, @"imput\Helium\Application\chrome.exe"),
            };
            var area = Screen.PrimaryScreen.WorkingArea;
            int w = Math.Min(1100, area.Width - 40), h = Math.Min(720, area.Height - 40);
            int x = area.Left + (area.Width - w) / 2, y = area.Top + (area.Height - h) / 2;
            string profile = Path.Combine(Path.GetTempPath(), "ArcherSetup-window");
            foreach (string exe in candidates)
            {
                if (!File.Exists(exe)) continue;
                try
                {
                    var psi = new ProcessStartInfo(exe, string.Format(
                        "--app=\"{0}\" --user-data-dir=\"{1}\" --window-size={2},{3} --window-position={4},{5} " +
                        "--no-first-run --no-default-browser-check --disable-background-mode --disable-sync " +
                        "--disable-features=Translate,msEdgeFirstRunExperience,msImplicitSignin --force-dark-mode",
                        url, profile, w, h, x, y));
                    psi.UseShellExecute = false;
                    return Process.Start(psi);
                }
                catch { }
            }
            Process.Start(url);  // no Chromium browser: the default browser still works, heartbeats end it
            return null;
        }
    }
}
