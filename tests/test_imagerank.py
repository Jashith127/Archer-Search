import httpx

from archer.app import app
from archer.imagerank import rank_images, similar_query
from archer.searxng import Result, SearXNG
from test_app import SEARX_REPLY, client, fake  # noqa: F401  (client is a fixture)


def res(title, img="https://img.example/x.jpg", engines=("bing images",), resolution="", url="https://shop.example/p"):
    return Result(title=title, url=url, host="shop.example", crumbs=[], content="", engines=list(engines),
                  img_src=img, thumbnail=img, resolution=resolution)


def test_model_number_matches_outrank_search_order():
    results = [
        res("MSI Gaming Logo PNG", "https://i/1.jpg"),
        res("Best MSI laptops 2022", "https://i/2.jpg"),
        res("MSI MAG255F E20 24.5 Rapid IPS", "https://i/3.jpg"),
    ]
    ranked = rank_images("MSI MAG 255F E20 24.5-Inch", results)
    assert ranked[0].img_src == "https://i/3.jpg"


def test_drops_duplicates_tiny_and_svg_icons():
    results = [
        res("MSI monitor", "https://i/a.jpg"),
        res("MSI monitor again", "https://i/a.jpg"),
        res("MSI monitor small", "https://i/b.jpg", resolution="120x90"),
        res("MSI icon", "https://i/c.svg"),
    ]
    assert [r.img_src for r in rank_images("msi monitor", results)] == ["https://i/a.jpg"]


def test_icons_kept_when_asked_for():
    assert rank_images("github logo svg", [res("GitHub logo", "https://i/g.svg")])


def test_similar_query_strips_site_suffix_and_falls_back():
    assert similar_query("MSI MAG 255F E20 Gaming Monitor | Amazon.in", "x") == "MSI MAG 255F E20 Gaming Monitor"
    assert similar_query("", "msi monitor") == "msi monitor"


def test_similar_api_excludes_current_image(client):
    def handler(request):
        assert request.url.params["q"] == "MSI MAG 255F E20 Gaming Monitor"
        return httpx.Response(200, json={**SEARX_REPLY, "results": [
            {"url": "https://a.example/1", "title": "MSI MAG 255F E20 Gaming Monitor", "img_src": "https://i/same.jpg", "engines": ["bing images"]},
            {"url": "https://a.example/2", "title": "MSI MAG 255F E20 front", "img_src": "https://i/other.jpg", "engines": ["bing images"]}]})

    app.state.searx = SearXNG(fake(handler))
    items = client.get("/api/similar", params={"title": "MSI MAG 255F E20 Gaming Monitor", "q": "msi", "exclude": "https://i/same.jpg"}).json()
    assert [i["full"] for i in items] == ["https://i/other.jpg"]


def test_images_tab_renders_viewer_panel(client):
    app.state.searx = SearXNG(fake(lambda r: httpx.Response(200, json={**SEARX_REPLY, "results": [
        {"url": "https://a.example/1", "title": "Zendaya red carpet", "img_src": "https://i/z.jpg", "engines": ["bing images"]}]})))
    html = client.get("/search", params={"q": "zendaya", "tab": "images"}).text
    assert "data-image-viewer" in html and 'data-full="https://i/z.jpg"' in html and "Similar images" in html
