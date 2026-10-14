// A deliberately small HTTP/1.1 server on 127.0.0.1: static UI files plus a JSON API.
// TcpListener rather than HttpListener, so no URL ACL or admin rights are involved.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace ArcherSetup
{
    static class Resources
    {
        public static Stream Open(string name)
        {
            return Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
        }

        public static byte[] Bytes(string name)
        {
            // --ui <dir>: serve the page from disk while working on it.
            if (Program.UiDir != null && name.StartsWith("ui/") && !name.StartsWith("ui/fonts/"))
            {
                string file = Path.Combine(Program.UiDir, name.Substring(3));
                if (File.Exists(file)) return File.ReadAllBytes(file);
            }
            using (var s = Open(name))
            {
                if (s == null) return null;
                var ms = new MemoryStream();
                s.CopyTo(ms);
                return ms.ToArray();
            }
        }

        public static string Text(string name)
        {
            var b = Bytes(name);
            return b == null ? "" : Encoding.UTF8.GetString(b);
        }
    }

    class Request
    {
        public string Method, Path, Body = "";
        public Dictionary<string, string> Query = new Dictionary<string, string>();
        public Dictionary<string, string> Headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        public Dictionary<string, object> Json()
        {
            if (string.IsNullOrEmpty(Body)) return new Dictionary<string, object>();
            var d = new JavaScriptSerializer().DeserializeObject(Body) as Dictionary<string, object>;
            return d ?? new Dictionary<string, object>();
        }

        public string Str(Dictionary<string, object> d, string key)
        {
            object v;
            return d.TryGetValue(key, out v) && v != null ? v.ToString() : "";
        }
    }

    class Server
    {
        public readonly int Port;
        public readonly string Token;
        readonly TcpListener listener;
        static readonly Dictionary<string, string> Types = new Dictionary<string, string>
        {
            { ".html", "text/html; charset=utf-8" }, { ".css", "text/css; charset=utf-8" },
            { ".js", "text/javascript; charset=utf-8" }, { ".woff2", "font/woff2" },
            { ".svg", "image/svg+xml" }, { ".png", "image/png" }, { ".ico", "image/x-icon" },
        };

        public Server(int port)
        {
            listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            Port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var bytes = new byte[18];
            using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes);
            Token = Convert.ToBase64String(bytes).Replace('+', '-').Replace('/', '_');
        }

        public void Start()
        {
            var t = new Thread(() =>
            {
                while (true)
                {
                    TcpClient client;
                    try { client = listener.AcceptTcpClient(); }
                    catch { return; }
                    ThreadPool.QueueUserWorkItem(_ => Handle(client));
                }
            });
            t.IsBackground = true;
            t.Start();
        }

        void Handle(TcpClient client)
        {
            using (client)
            {
                try
                {
                    client.ReceiveTimeout = 15000;
                    var stream = client.GetStream();
                    var req = Read(stream);
                    if (req == null) return;
                    Respond(stream, req);
                }
                catch (Exception e)
                {
                    try { Write(client.GetStream(), 500, "text/plain", Encoding.UTF8.GetBytes(e.Message)); } catch { }
                }
            }
        }

        static Request Read(NetworkStream stream)
        {
            var head = new MemoryStream();
            int matched = 0;
            while (matched < 4)
            {
                int b = stream.ReadByte();
                if (b < 0) return null;
                head.WriteByte((byte)b);
                matched = (b == (matched % 2 == 0 ? '\r' : '\n')) ? matched + 1 : (b == '\r' ? 1 : 0);
                if (head.Length > 32768) return null;
            }
            var lines = Encoding.ASCII.GetString(head.ToArray()).Split(new[] { "\r\n" }, StringSplitOptions.None);
            var first = lines[0].Split(' ');
            if (first.Length < 2) return null;
            var req = new Request { Method = first[0] };
            string target = first[1];
            int q = target.IndexOf('?');
            req.Path = q < 0 ? target : target.Substring(0, q);
            if (q >= 0)
            {
                foreach (var pair in target.Substring(q + 1).Split('&'))
                {
                    int eq = pair.IndexOf('=');
                    if (eq > 0) req.Query[Uri.UnescapeDataString(pair.Substring(0, eq))] = Uri.UnescapeDataString(pair.Substring(eq + 1).Replace('+', ' '));
                }
            }
            for (int i = 1; i < lines.Length; i++)
            {
                int c = lines[i].IndexOf(':');
                if (c > 0) req.Headers[lines[i].Substring(0, c).Trim()] = lines[i].Substring(c + 1).Trim();
            }
            string len;
            if (req.Headers.TryGetValue("Content-Length", out len))
            {
                int n = Math.Min(int.Parse(len), 1 << 20);
                var body = new byte[n];
                int got = 0;
                while (got < n)
                {
                    int r = stream.Read(body, got, n - got);
                    if (r <= 0) break;
                    got += r;
                }
                req.Body = Encoding.UTF8.GetString(body, 0, got);
            }
            return req;
        }

        void Respond(NetworkStream stream, Request req)
        {
            // DNS-rebinding guard: only answer requests addressed to our own loopback origin.
            string host;
            req.Headers.TryGetValue("Host", out host);
            if (host != "127.0.0.1:" + Port)
            {
                Write(stream, 403, "text/plain", Encoding.UTF8.GetBytes("Forbidden"));
                return;
            }

            if (req.Path.StartsWith("/api/"))
            {
                // Every API call carries the per-run token in a custom header, which a cross-origin page
                // can't send without a CORS preflight that this server never approves.
                string token;
                req.Headers.TryGetValue("X-Setup-Token", out token);
                if (token != Token)
                {
                    Write(stream, 403, "text/plain", Encoding.UTF8.GetBytes("Forbidden"));
                    return;
                }
                Program.LastPing = DateTime.UtcNow;
                object result;
                int status = 200;
                try { result = Api.Handle(req); }
                catch (Exception e)
                {
                    status = 500;
                    result = new Dictionary<string, object> { { "error", e.Message } };
                }
                var json = new JavaScriptSerializer { MaxJsonLength = 1 << 22 }.Serialize(result);
                Write(stream, status, "application/json; charset=utf-8", Encoding.UTF8.GetBytes(json));
                return;
            }

            string name = req.Path == "/" ? "index.html" : req.Path.TrimStart('/');
            if (name.Contains("..") || name.Contains("/") && !name.StartsWith("fonts/"))
            {
                Write(stream, 404, "text/plain", Encoding.UTF8.GetBytes("Not found"));
                return;
            }
            var bytes = Resources.Bytes("ui/" + name);
            if (bytes == null)
            {
                Write(stream, 404, "text/plain", Encoding.UTF8.GetBytes("Not found"));
                return;
            }
            if (name == "index.html")
            {
                string page = Encoding.UTF8.GetString(bytes).Replace("{{token}}", Token).Replace("{{version}}", Program.Version);
                bytes = Encoding.UTF8.GetBytes(page);
            }
            string type;
            if (!Types.TryGetValue(Path.GetExtension(name), out type)) type = "application/octet-stream";
            Write(stream, 200, type, bytes);
        }

        static void Write(NetworkStream stream, int status, string type, byte[] body)
        {
            string reason = status == 200 ? "OK" : status == 403 ? "Forbidden" : status == 404 ? "Not Found" : "Error";
            var head = new StringBuilder();
            head.Append("HTTP/1.1 ").Append(status).Append(' ').Append(reason).Append("\r\n");
            head.Append("Content-Type: ").Append(type).Append("\r\n");
            head.Append("Content-Length: ").Append(body.Length).Append("\r\n");
            head.Append("Cache-Control: no-store\r\n");
            head.Append("Referrer-Policy: no-referrer\r\n");
            head.Append("X-Content-Type-Options: nosniff\r\n");
            head.Append("Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'\r\n");
            head.Append("Connection: close\r\n\r\n");
            var h = Encoding.ASCII.GetBytes(head.ToString());
            stream.Write(h, 0, h.Length);
            stream.Write(body, 0, body.Length);
            stream.Flush();
        }
    }
}
