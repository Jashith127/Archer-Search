"""Song rescue and lyric matching use only mocked source results."""

import asyncio

import httpx

from archer.searxng import Result
from archer.sources import lyrics
from archer.views import _song_from_results


def _result(title: str, url: str, host: str) -> Result:
    return Result(title=title, url=url, host=host, crumbs=[], content="", engines=["duckduckgo"])


def test_lyrics_result_rescues_song_without_wikipedia_page():
    results = [
        _result("Travis Scott – Maria I'm Drunk Lyrics | Genius Lyrics",
                "https://genius.com/Travis-scott-maria-im-drunk-lyrics", "genius.com"),
        _result("Maria I'm Drunk - YouTube", "https://www.youtube.com/watch?v=abcdefghijk", "youtube.com"),
        _result("Maria I'm Drunk - Travis Scott", "https://open.spotify.com/track/1234567890ABCDEFGHIJKL", "open.spotify.com"),
    ]
    hero = _song_from_results("mariah im drunk", results)
    assert hero.kind == "song"
    assert hero.data["title"] == "Maria I'm Drunk"
    assert hero.data["artist"] == "Travis Scott"
    assert hero.data["video_id"] == "abcdefghijk"
    assert hero.data["spotify_id"] == "1234567890ABCDEFGHIJKL"
    assert _song_from_results("Mariah Carey", results) is None


def test_lyrics_lookup_prefers_matching_track_and_artist():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "lrclib.net"
        return httpx.Response(200, json=[
            {"id": 1, "trackName": "Lean On", "artistName": "Other Artist", "plainLyrics": "Wrong"},
            {"id": 2, "trackName": "Lean On", "artistName": "Major Lazer", "plainLyrics": "Line one\nLine two"},
        ])

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await lyrics.lookup(client, "Lean On", "Major Lazer")

    lyrics._cache._data.clear()
    found = asyncio.run(fetch())
    assert found["text"] == "Line one\nLine two"
    assert found["url"] == "https://lrclib.net/tracks/2"


def test_spotify_track_found_from_search_results():
    from archer.searxng import Result
    from archer.views import spotify_from_results

    def r(url, title, content=""):
        return Result(title=title, url=url, host="open.spotify.com", crumbs=[], content=content, engines=["google"])
    results = [r("https://open.spotify.com/track/1111111111111111111111", "Blinding Lights (Remix) - song by Someone Else | Spotify"),
               r("https://open.spotify.com/intl-de/track/0VjIjW4GlUZAMYd2vXMi3b?si=x", "Blinding Lights - song and lyrics by The Weeknd | Spotify")]
    found = spotify_from_results(results, "Blinding Lights", "The Weeknd")
    assert found == {"embed": "https://open.spotify.com/embed/track/0VjIjW4GlUZAMYd2vXMi3b",
                     "url": "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b"}
    assert spotify_from_results(results, "Save Your Tears", "The Weeknd") is None
    assert spotify_from_results([r("https://open.spotify.com/search/blinding", "Blinding Lights - Spotify search")], "Blinding Lights") is None
