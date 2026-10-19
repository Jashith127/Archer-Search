"""Small public-page reader. Resolve once, validate all addresses, connect to a pinned IP.

The original hostname is retained as HTTP Host and TLS SNI/certificate name. This
prevents a second DNS lookup from rebinding to a local service. No ambient proxies,
cookies, browser credentials, JavaScript or automatic redirects are used.
"""
import asyncio
from html.parser import HTMLParser
import ipaddress
import re
import socket
import zlib
from urllib.parse import urljoin, urlsplit

import httpx

from archer import tls
from archer.evidence import public_url
from archer.sources import TTLCache, USER_AGENT

MAX_BYTES = 2_000_000


class TextReader(HTMLParser):
    """Page text with the furniture removed.

    Beyond script/style and the landmark tags, list containers are dropped when
    they are navigation rather than content. Forum and wiki pages routinely open
    with hundreds of characters of link lists — thread titles, sidebars, tag
    clouds — and those windows then win any term-overlap ranking against the real
    article. That is how a 7200-character VLR.gg navigation blob ended up as the
    "read page" behind the answer to "riot vs valve".

    A list is treated as chrome when it is link-dense (most of its characters sit
    inside anchors) or when it sits inside a container marked navigational.
    Article lists — specifications, findings, steps, comparisons — keep their text.
    """

    HIDDEN_TAGS = ("script", "style", "noscript", "svg", "nav", "footer", "header")
    LIST_TAGS = ("ul", "ol", "dl")
    NAVISH = re.compile(
        r"(^|[^a-z])(nav|menu|sidebar|breadcrumb|masthead|banner|promo|related|social|"
        r"comment|toolbar|pagination|tag-?cloud|widget|module)s?([^a-z]|$)", re.I)
    # Only these are treated as navigational by class/id alone. They are semantic
    # containers that real markup closes, so an omitted end tag cannot make one
    # swallow the whole article. Generic <div class="sidebar"> is left to the
    # link-density rule, which cannot over-fire on prose.
    NAVISH_TAGS = ("nav", "aside", "section", "header", "footer", "menu")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        # One entry per open element: (tag, hidden, navish, opened_list). A stack
        # is required rather than a counter: an end tag must decrement only the
        # element that actually opened it.
        self._stack = []
        self._link_depth = 0     # depth inside <a>
        self._list = None        # {"links", "text", "buffer"} while a list is open

    def _chrome(self) -> bool:
        return any(hidden or navish for _, hidden, navish, _ in self._stack) \
            or bool((self._list or {}).get("dropped"))

    @staticmethod
    def _blob(attrs) -> str:
        """Attribute names and values as bare space-separated tokens, so NAVISH sees
        `class sidebar` rather than `class=sidebar`."""
        tokens = []
        for key, value in attrs:
            tokens.append(key or "")
            if value:
                tokens.extend(re.split(r"[^A-Za-z0-9]+", value))
        return " ".join(tokens)

    def handle_starttag(self, tag, attrs):
        hidden = tag in self.HIDDEN_TAGS
        navish = tag in self.NAVISH_TAGS and bool(self.NAVISH.search(self._blob(attrs)))
        opened_list = False
        if tag == "a":
            self._link_depth += 1
        if tag in self.LIST_TAGS and self._list is None and not hidden:
            self._list = {"links": 0, "text": 0, "buffer": [],
                          "chrome": any(n for _, _, n, _ in self._stack)}
            opened_list = True
        self._stack.append((tag, hidden, navish, opened_list))
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "tr", "br") and not self._chrome():
            self._emit("\n")

    def handle_endtag(self, tag):
        # Pop through the matching open element so counters stay balanced even
        # when the markup omits optional end tags.
        index = next((i for i in range(len(self._stack) - 1, -1, -1)
                      if self._stack[i][0] == tag), None)
        if index is None:
            if tag == "a":
                self._link_depth = max(0, self._link_depth - 1)
            return
        while len(self._stack) > index:
            open_tag, hidden, navish, opened_list = self._stack.pop()
            if open_tag == "a":
                self._link_depth = max(0, self._link_depth - 1)
            if opened_list and self._list is not None:
                entry = self._list
                # Buffered, so the decision can be made before anything is kept:
                # link-dense lists are link farms, text-bearing lists are content.
                linky = entry["links"] >= 40 and entry["links"] >= 2 * max(1, entry["text"])
                if not (entry["chrome"] or linky):
                    self.parts.extend(entry["buffer"])
                    self.parts.append("\n")
                self._list = None

    def handle_startendtag(self, tag, attrs):
        # Self-closing: contributes a break but opens no state.
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "tr", "br") and not self._chrome():
            self._emit("\n")

    def _emit(self, text: str) -> None:
        """Text goes to the open list's buffer when there is one, so a link farm can
        still be discarded at close."""
        if self._list is not None:
            self._list["buffer"].append(text)
        else:
            self.parts.append(text)

    def handle_data(self, data):
        if any(hidden for _, hidden, _, _ in self._stack):
            return
        if self._list is not None:
            stripped = len(data.strip())
            if self._link_depth:
                self._list["links"] += stripped
            else:
                self._list["text"] += stripped
        if self._chrome():
            return
        self._emit(data)


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
        text, kind, _ = await fetch_html(url, MAX_BYTES)
        if kind != "text/plain":
            parser = TextReader()
            parser.feed(text)
            text = " ".join(parser.parts)
        lines = [" ".join(line.split()) for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
        if len(text) < 80:
            raise ValueError("Not enough readable text.")
        return text[:24000]


async def fetch_html(url: str, limit: int, *, head: bool = False) -> tuple[str, str, str]:
    """(text, content type, final URL) of a public page. `head` keeps the first `limit` bytes
    instead of failing on a longer page, for reading only the <head>."""
    original = url
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5, verify=tls.context()) as client:
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
                content, wire_bytes, cut = bytearray(), 0, False
                async for chunk in response.aiter_raw(chunk_size=16384):
                    wire_bytes += len(chunk)
                    if wire_bytes > MAX_BYTES:
                        raise ValueError("Page exceeds the transfer limit.")
                    try:
                        decoded = inflater.decompress(chunk, limit-len(content)+1) if inflater else chunk
                    except zlib.error as exc:
                        raise ValueError("Invalid compressed page.") from exc
                    content.extend(decoded)
                    if len(content) > limit:
                        if not head:
                            raise ValueError("Page exceeds the reading limit.")
                        del content[limit:]
                        cut = True
                        break
                if inflater and not inflater.eof and not cut:
                    raise ValueError("Incomplete compressed page.")
                return content.decode(response.encoding or "utf-8", errors="replace"), kind, url
    raise ValueError(f"Too many redirects reading {urlsplit(original).hostname}.")


class _LeadImage(HTMLParser):
    """og:image (or twitter:image / image_src) from a page's <head>."""
    ORDER = ("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src", "thumbnailurl", "image_src")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = {}

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key in self.ORDER and a.get("content"):
                self.found.setdefault(key, a["content"].strip())
        elif tag == "link" and a.get("rel", "").lower() == "image_src" and a.get("href"):
            self.found.setdefault("image_src", a["href"].strip())

    def best(self):
        return next((self.found[k] for k in self.ORDER if k in self.found), "")


_lead_cache = TTLCache(86400, 1024)
_lead_slots = asyncio.Semaphore(4)


async def lead_image(url: str) -> str:
    """The picture a page shows when it is shared (its og:image): a news card's thumbnail when
    the search engine sent none. Empty when the page has none or can't be read."""
    url = public_url(url)
    if not url:
        return ""
    if (hit := _lead_cache.get(url)) is not None:
        return hit
    found = ""
    try:
        async with _lead_slots, asyncio.timeout(6):
            text, kind, final = await fetch_html(url, 300_000, head=True)
        if kind != "text/plain":
            parser = _LeadImage()
            parser.feed(text.split("</head>", 1)[0])
            found = public_url(urljoin(final, parser.best())) if parser.best() else ""
    except (ValueError, OSError, httpx.HTTPError, TimeoutError):
        found = ""
    return _lead_cache.put(url, found)
