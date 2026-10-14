"""Small public-page reader. Resolve once, validate all addresses, connect to a pinned IP.

The original hostname is retained as HTTP Host and TLS SNI/certificate name. This
prevents a second DNS lookup from rebinding to a local service. No ambient proxies,
cookies, browser credentials, JavaScript or automatic redirects are used.
"""
import asyncio
from html.parser import HTMLParser
import ipaddress
import socket
import zlib
from urllib.parse import urljoin, urlsplit

import httpx

from archer.evidence import public_url
from archer.sources import TTLCache, USER_AGENT

MAX_BYTES = 2_000_000


class TextReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self.hidden += 1
        if tag in ("p", "div", "li", "h1", "h2", "h3", "tr", "br") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


async def pinned_address(host: str, port: int) -> str:
    values = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = [v[4][0] for v in values]
    def allowed(value):
        address = ipaddress.ip_address(value)
        return address.is_global and not address.is_multicast and not address.is_reserved
    if not addresses or not all(allowed(a) for a in addresses):
        raise ValueError("Only public Internet pages can be read.")
    return addresses[0]


class PageReader:
    def __init__(self):
        self.cache = TTLCache(600, 128)
        self.pending = {}
        self.slots = asyncio.Semaphore(2)
        self.waiters = {}

    async def read(self, url: str) -> str:
        url = public_url(url)
        if not url:
            raise ValueError("Unsupported page URL.")
        if (hit := self.cache.get(url)) is not None:
            return hit
        if url not in self.pending:
            task = asyncio.create_task(self._bounded(url))
            self.pending[url] = task
            task.add_done_callback(lambda t: self.pending.pop(url, None))
        task = self.pending[url]
        self.waiters[url] = self.waiters.get(url, 0) + 1
        try:
            return await asyncio.shield(task)
        finally:
            self.waiters[url] -= 1
            if not self.waiters[url]:
                self.waiters.pop(url, None)
                if not task.done():
                    task.cancel()

    async def _bounded(self, url):
        async with self.slots, asyncio.timeout(5):
            text = await self._read(url)
            return self.cache.put(url, text)

    async def _read(self, url):
        original = url
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5) as client:
            for _ in range(4):
                p = urlsplit(url)
                port = p.port or (443 if p.scheme == "https" else 80)
                ip = await pinned_address(p.hostname, port)
                target = httpx.URL(url).copy_with(host=ip)
                host = p.netloc.encode("idna").decode("ascii")
                async with client.stream("GET", target, headers={
                    "Host": host, "User-Agent": USER_AGENT, "Accept": "text/html,text/plain",
                    "Accept-Encoding": "gzip, deflate", "Cookie": "",
                }, extensions={"sni_hostname": p.hostname}) as response:
                    if response.is_redirect:
                        url = public_url(urljoin(url, response.headers.get("location", "")))
                        if not url:
                            raise ValueError("Unsupported redirect.")
                        continue
                    response.raise_for_status()
                    encoding = response.headers.get("content-encoding", "identity").lower()
                    if encoding not in ("", "identity", "gzip", "deflate"):
                        raise ValueError("Unsupported page compression.")
                    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS) if encoding in ("gzip", "deflate") else None
                    kind = response.headers.get("content-type", "").split(";")[0]
                    if kind not in ("text/html", "text/plain", "application/xhtml+xml"):
                        raise ValueError("This page is not readable text.")
                    content, wire_bytes = bytearray(), 0
                    async for chunk in response.aiter_raw(chunk_size=16384):
                        wire_bytes += len(chunk)
                        if wire_bytes > MAX_BYTES:
                            raise ValueError("Page exceeds the transfer limit.")
                        try:
                            decoded = inflater.decompress(chunk, MAX_BYTES-len(content)+1) if inflater else chunk
                        except zlib.error as exc:
                            raise ValueError("Invalid compressed page.") from exc
                        content.extend(decoded)
                        if len(content) > MAX_BYTES:
                            raise ValueError("Page exceeds the reading limit.")
                    if inflater and not inflater.eof:
                        raise ValueError("Incomplete compressed page.")
                    text = content.decode(response.encoding or "utf-8", errors="replace")
                    if kind != "text/plain":
                        parser = TextReader()
                        parser.feed(text)
                        text = " ".join(parser.parts)
                    lines = [" ".join(line.split()) for line in text.splitlines()]
                    text = "\n".join(line for line in lines if line)
                    if len(text) < 80:
                        raise ValueError("Not enough readable text.")
                    return text[:24000]
        raise ValueError(f"Too many redirects reading {urlsplit(original).hostname}.")
