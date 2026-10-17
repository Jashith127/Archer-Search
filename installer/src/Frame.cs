// The installer window: a borderless form hosting the page in WebView2, the Edge engine Windows
// ships. It keeps the system shadow, rounded corners and minimise animation but has no title bar;
// the page draws its own controls, marks its drag strip with `app-region: drag`, and posts
// "drag", "minimize" and "close" for the rest. Without the WebView2 runtime, Program falls back to
// a Chromium --app window.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace ArcherSetup
{
    // Makes the embedded WebView2 assemblies and loader available. Holds no WebView2 types, so Main
    // can run it before anything that needs them is compiled.
    static class WebView2Files
    {
        public static void Hook()
        {
            AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
            {
                string name = new AssemblyName(e.Name).Name;
                if (!name.StartsWith("Microsoft.Web.WebView2.")) return null;
                var bytes = Resources.Bytes("lib/" + name + ".dll");
                return bytes == null ? null : Assembly.Load(bytes);
            };
        }

        // WebView2Loader.dll for this process's architecture, written once per version into %TEMP%.
        public static string LoaderDir()
        {
            string arch = !Environment.Is64BitProcess ? "x86"
                : (Environment.GetEnvironmentVariable("PROCESSOR_ARCHITECTURE") ?? "").Equals("ARM64", StringComparison.OrdinalIgnoreCase) ? "arm64" : "x64";
            var bytes = Resources.Bytes("native/" + arch + "/WebView2Loader.dll");
            if (bytes == null) return null;
            string dir = Path.Combine(Path.GetTempPath(), "ArcherSetup", Program.Version, arch);
            string file = Path.Combine(dir, "WebView2Loader.dll");
            if (!File.Exists(file) || new FileInfo(file).Length != bytes.Length)
            {
                Directory.CreateDirectory(dir);
                File.WriteAllBytes(file, bytes);
            }
            return dir;
        }
    }

    static class Dpi
    {
        [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
        [DllImport("shcore.dll")] static extern int SetProcessDpiAwareness(int value);
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

        // Per-monitor aware, so the window and WebView2 render sharply at any scale.
        public static void Aware()
        {
            try { if (SetProcessDpiAwarenessContext(new IntPtr(-4))) return; } catch { }
            try { if (SetProcessDpiAwareness(2) == 0) return; } catch { }
            try { SetProcessDPIAware(); } catch { }
        }

        public static double Scale()
        {
            using (var g = Graphics.FromHwnd(IntPtr.Zero)) return g.DpiX / 96.0;
        }
    }

    class Frame : Form
    {
        // The page is designed for this size, in CSS pixels.
        public const int PageWidth = 760, PageHeight = 660;
        const int WS_CAPTION = 0x00C00000, WS_SYSMENU = 0x00080000, WS_MINIMIZEBOX = 0x00020000;
        const int WM_NCCALCSIZE = 0x0083, WM_NCACTIVATE = 0x0086, WM_NCLBUTTONDOWN = 0x00A1, WM_DPICHANGED = 0x02E0, HTCAPTION = 2;

        [StructLayout(LayoutKind.Sequential)] struct Margins { public int Left, Right, Top, Bottom; }
        [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left, Top, Right, Bottom; }
        [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
        [DllImport("dwmapi.dll")] static extern int DwmExtendFrameIntoClientArea(IntPtr hwnd, ref Margins margins);
        [DllImport("user32.dll")] static extern bool ReleaseCapture();
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr w, IntPtr l);
        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
        [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);

        readonly string url;
        readonly WebView2 view = new WebView2();
        readonly ApplicationContext context = new ApplicationContext();
        bool force, failed;

        Frame(string url)
        {
            this.url = url;
            Text = "Archer Setup";
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            BackColor = Color.FromArgb(10, 10, 10);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            double s = Dpi.Scale();
            var area = Screen.PrimaryScreen.WorkingArea;
            int w = Math.Min((int)Math.Round(PageWidth * s), area.Width - 24), h = Math.Min((int)Math.Round(PageHeight * s), area.Height - 24);
            Bounds = new Rectangle(area.Left + (area.Width - w) / 2, area.Top + (area.Height - h) / 2, w, h);
            view.Dock = DockStyle.Fill;
            view.DefaultBackgroundColor = BackColor;
            Controls.Add(view);
        }

        // A caption style keeps the shadow, snap and minimise animation; WM_NCCALCSIZE then gives the
        // whole window to the client area, so no title bar is drawn.
        protected override CreateParams CreateParams
        {
            get
            {
                var cp = base.CreateParams;
                cp.Style |= WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
                return cp;
            }
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            int on = 1, round = 2, border = 0x002A2A2A;  // COLORREF, 0x00BBGGRR
            DwmSetWindowAttribute(Handle, 20, ref on, 4);       // dark mode for the system menu
            DwmSetWindowAttribute(Handle, 33, ref round, 4);    // Windows 11 rounded corners
            DwmSetWindowAttribute(Handle, 34, ref border, 4);   // a hairline border to match the page
            var m = new Margins { Top = 1 };                     // lets Windows 10 draw the shadow
            DwmExtendFrameIntoClientArea(Handle, ref m);
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_NCCALCSIZE && m.WParam != IntPtr.Zero) { m.Result = IntPtr.Zero; return; }
            if (m.Msg == WM_NCACTIVATE) m.LParam = new IntPtr(-1);  // don't repaint a caption on focus changes
            if (m.Msg == WM_DPICHANGED)
            {
                var r = (Rect)Marshal.PtrToStructure(m.LParam, typeof(Rect));
                Bounds = new Rectangle(r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top);
                m.Result = IntPtr.Zero;
                return;
            }
            base.WndProc(ref m);
        }

        // Closing while a job runs asks the page first; it answers by calling /api/quit.
        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!force && Jobs.AnyRunning() && view.CoreWebView2 != null)
            {
                e.Cancel = true;
                view.CoreWebView2.PostWebMessageAsString("close-requested");
                return;
            }
            base.OnFormClosing(e);
        }

        async void Start()
        {
            try
            {
                string data = Path.Combine(Path.GetTempPath(), "ArcherSetup-webview");
                var env = await CoreWebView2Environment.CreateAsync(null, data);
                await view.EnsureCoreWebView2Async(env);
            }
            catch
            {
                failed = true;
                context.ExitThread();
                return;
            }
            var core = view.CoreWebView2;
            var settings = core.Settings;
            settings.AreDefaultContextMenusEnabled = false;
            settings.AreDevToolsEnabled = Program.Dry;
            settings.AreBrowserAcceleratorKeysEnabled = Program.Dry;
            settings.IsZoomControlEnabled = false;
            settings.IsStatusBarEnabled = false;
            settings.IsGeneralAutofillEnabled = false;
            settings.IsPasswordAutosaveEnabled = false;
            try { settings.IsSwipeNavigationEnabled = false; settings.IsPinchZoomEnabled = false; } catch { }
            try { settings.IsNonClientRegionSupportEnabled = true; } catch { }  // app-region: drag (runtime 123+)

            string origin = url.TrimEnd('/');
            core.NavigationStarting += (s, e) =>
            {
                if (e.Uri.StartsWith(origin)) return;
                e.Cancel = true;
                External(e.Uri);
            };
            core.NewWindowRequested += (s, e) => { e.Handled = true; External(e.Uri); };
            core.WebMessageReceived += (s, e) =>
            {
                try { OnPageMessage(e.TryGetWebMessageAsString()); } catch (ArgumentException) { }
            };
            bool shown = false;
            Action reveal = () =>
            {
                if (shown) return;
                shown = true;
                Show();
                Activate();
            };
            core.NavigationCompleted += (s, e) => reveal();
            var late = new System.Windows.Forms.Timer { Interval = 6000 };
            late.Tick += (s, e) => { late.Stop(); reveal(); };
            late.Start();
            core.Navigate(url);
        }

        void OnPageMessage(string message)
        {
            if (message == "minimize") WindowState = FormWindowState.Minimized;
            else if (message == "close") Close();
            else if (message == "drag")
            {
                // Only when the button is still down: a move loop started after release would carry
                // the window until the next click.
                int button = GetSystemMetrics(23) != 0 ? 0x02 : 0x01;  // SM_SWAPBUTTON
                if (GetAsyncKeyState(button) >= 0) return;
                ReleaseCapture();
                SendMessage(Handle, WM_NCLBUTTONDOWN, new IntPtr(HTCAPTION), IntPtr.Zero);
            }
        }

        static void External(string uri)
        {
            if (uri.StartsWith("http://") || uri.StartsWith("https://"))
                try { Process.Start(uri); } catch { }
        }

        // Shows the page and returns once the window has closed; false when WebView2 isn't usable,
        // so the caller can open a browser window instead.
        public static bool Run(string url)
        {
            try
            {
                string loader = WebView2Files.LoaderDir();
                if (loader == null) return false;
                CoreWebView2Environment.SetLoaderDllFolderPath(loader);
                CoreWebView2Environment.GetAvailableBrowserVersionString();
            }
            catch { return false; }

            Application.EnableVisualStyles();
            var frame = new Frame(url);
            frame.FormClosed += (s, e) => frame.context.ExitThread();
            // Handles without showing: the window appears once the page has loaded.
            GC.KeepAlive(frame.Handle);
            GC.KeepAlive(frame.view.Handle);
            var quit = new Thread(() =>
            {
                Program.Quit.WaitOne();
                try { frame.BeginInvoke((Action)(() => { frame.force = true; frame.Close(); })); } catch { }
            });
            quit.IsBackground = true;
            quit.Start();
            frame.Start();
            Application.Run(frame.context);
            return !frame.failed;
        }
    }
}
