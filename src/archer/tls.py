"""One TLS context for every outgoing HTTPS client.

httpx builds a fresh context per client by loading certifi's CA bundle, which takes about 0.4 s on
Windows. Archer makes several clients (search, sources, AI calls, page reads), so they share this one;
the launcher builds it in the background while the app is still importing.
"""
import ssl
import threading

_lock = threading.Lock()
_context: ssl.SSLContext | None = None


def context() -> ssl.SSLContext:
    global _context
    if _context is None:
        with _lock:
            if _context is None:
                import httpx
                _context = httpx.create_ssl_context()
    return _context
