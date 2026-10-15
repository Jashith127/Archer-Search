"""Product panels from search listings: prices, model matching, detection, reviews and the AI card."""

import json

from archer.ai_service import store_listing, validate_block
from archer.evidence import Evidence, product_facts
from archer.query import classify
from archer.searxng import Result
from archer.sources import products


def result(host, title, content="", thumb=""):
    return Result(title=title, url=f"https://{host}/p/{abs(hash(title)) % 10**6}", host=host, crumbs=[], content=content,
                  engines=["google"], thumbnail=thumb)


LISTINGS = [
    result("sony.co.in", "WH-1000XM5 | Wireless Noise Cancelling Headphones - Sony India",
           "Our ultra comfortable WH-1000XM5 headphones rewrite the rules for distraction-free listening."),
    result("amazon.in", "Sony WH-1000XM5 Wireless Headphones -Black : Amazon.in: Electronics",
           "4.4 out of 5 stars 12,345 ratings. Save ₹5,000 with bank offers. ₹29,990", "https://m.media-amazon.com/x.jpg"),
    result("flipkart.com", "SONY WH-1000XM5 Wireless Noise Cancellation (Midnight Blue)",
           "Buy SONY WH-1000XM5 for Rs.34990 Online ; 30 Hours Battery Life ; 8 Microphones for ANC"),
    result("croma.com", "Sony WH-1000XM5 price drop", "Now ₹24,990. No cost EMI from ₹2,083/month."),
    result("amazon.in", "Ear Pads Replacement for Sony WH-1000XM5", "Cushions ₹999"),
    result("smartprix.com", "Sony WH-1000XM5 Price in India", "Lowest price ₹19,489 at Amazon"),
    result("soundguys.com", "Sony WH-1000XM5 review - SoundGuys",
           "20 May 2022 ... The Sony WH-1000XM5 headphones offer decent sound quality with strong bass"),
    result("recordingnow.com", "Sony WH-1000XM5 Review: Still WORTH IT?", "Overall: 8.50 / 10 ... one of the better all-arounders"),
    result("youtube.com", "Sony WH-1000XM5 Review: Two Steps Forward", "12 May 2022"),
    result("sony.co.in", "WH-1000XM6 | The best noise cancelling", "The new WH-1000XM6"),
]


def test_prices_skip_discounts_emis_and_monthly_figures():
    assert products.find_price("Save ₹5,000 with bank offers. ₹29,990")["amount"] == 29990
    assert products.find_price("No cost EMI from ₹2,083/month. Now ₹24,990")["amount"] == 24990
    assert products.find_price("Buy for Rs.34990 Online")["amount"] == 34990
    assert products.find_price("Apple iPhone 16 Pro price is Rs. 1,09,900.")["amount"] == 109900
    lakh = products.find_price("Price ₹ 33.43 - 51.44 Lakh*")
    assert lakh["amount"] == 3343000 and lakh["high"] == 5144000
    assert products.find_price("$999.99 at Best Buy") == {"currency": "USD", "amount": 999.99, "high": 0.0}
    assert products.find_price("Flat ₹2,000 off") is None and products.find_price("Rated 4/5") is None


def test_indian_grouping_and_scores():
    assert products.fmt_money(109900, "INR") == "₹1,09,900"
    assert products.fmt_money(24990, "INR") == "₹24,990"
    assert products.fmt_money(1099, "USD") == "$1,099"
    assert products.review_score("Overall: 8.50 / 10") == (8.5, "8.50/10")
    assert products.review_score("Our verdict: 4.5 out of 5") == (9.0, "4.5/5")
    assert products.store_rating("4.4 out of 5 stars 12,345 ratings") == (4.4, 12345)


def test_matcher_tells_models_apart():
    pro = products.Matcher("iphone 16 pro")
    assert pro("Apple iPhone 16 Pro (Natural Titanium, 128 GB)")
    assert not pro("Apple iPhone 16 Pro Max (256 GB)") and not pro("iPhone 16 Plus")
    xm5 = products.Matcher("sony wh-1000xm5")
    assert xm5("WH1000XM5 headphones") and xm5("WH-1000XM5 | Sony India") and not xm5("WH-1000XM6")


def test_detects_listed_models_but_not_categories_or_ingredients():
    assert products.detect("sony wh-1000xm5", LISTINGS)
    assert not products.detect("best headphones under 30000", LISTINGS)
    groceries = [result("amazon.in", "India Gate Basmati Rice 5kg", "₹699"), result("flipkart.com", "Daawat Basmati Rice", "₹540"),
                 result("jiomart.com", "Fortune Rice 10 kg", "₹899")]
    assert not products.detect("rice", groceries)


def test_panel_from_listings():
    p = products.panel("sony wh-1000xm5", LISTINGS, intent="price", related=["sony wh-1000xm5 vs bose qc ultra"])
    assert p["name"] == "Sony WH-1000XM5" and p["brand"] == "Sony" and p["category"] == "Headphones"
    stores = {o["store"]: o for o in p["offers"]}
    assert "Ear Pads" not in json.dumps(p["offers"])                       # accessories are not this product
    assert stores["Croma"]["price"] == 24990 and stores["Croma"]["best"]
    assert stores["Amazon"]["price"] == 29990 and stores["Amazon"]["rating"] == 4.4
    assert stores["Sony"]["role"] == "official" and stores["Smartprix"]["role"] == "guide"
    assert p["price"]["low_text"] == "₹24,990" and p["price"]["high_text"] == "₹34,990" and p["price"]["stores"] == 3
    assert p["offers"][0]["store"] == "Croma"                              # cheapest store first, trackers after stores
    assert [r["site"] for r in p["reviews"]][:2] == ["Recordingnow", "SoundGuys"]  # scored first; no YouTube
    assert p["reviews"][1]["date"] == "20 May 2022" and p["score"] == {"value": 8.5, "count": 1}
    assert p["rivals"] == ["Bose QC Ultra"] or p["rivals"][0].lower() == "bose qc ultra"
    assert "Sony WH-1000XM5 vs" in p["questions"][1]
    assert p["tab"] == "prices" and "30 Hours Battery Life" in p["highlights"]
    assert p["variants"]["colors"] == [{"name": "Midnight Blue", "hex": products.swatch("midnight")}]


def test_query_strips_shopping_words():
    q = classify("iphone 16 pro price in india")
    assert q.entity_candidate == "iphone 16 pro" and q.product_intent == "price"
    assert classify("buy kindle paperwhite").entity_candidate == "kindle paperwhite"
    assert classify("galaxy s24 ultra review").product_intent == "review"
    assert classify("dune").product_intent == ""


def test_products_block_citing_the_card_links_the_named_store():
    card = Evidence("S1", "Archer product card", "https://www.sony.co.in/x", json.dumps(product_facts(products.panel("sony wh-1000xm5", LISTINGS)), ensure_ascii=False), kind="card")
    croma = next(o["url"] for o in products.panel("sony wh-1000xm5", LISTINGS)["offers"] if o["store"] == "Croma")
    assert store_listing(card, "Croma (cheapest found)") == croma and store_listing(card, "Bose QC Ultra") == ""
    block = validate_block({"kind": "products", "items": [{"label": "Croma", "value": "₹24,990", "text": "Lowest store price", "citations": ["S1"]}]},
                           [card])
    assert block["items"][0]["url"] == croma


def test_specs_block_needs_labels_and_text():
    card = Evidence("S1", "Archer product card", "", '{"specs": [["Weight", "199 g"]]}', kind="card")
    block = validate_block({"kind": "specs", "items": [{"label": "Weight", "text": "199 g", "citations": ["S1"]}]}, [card])
    assert block["items"][0]["label"] == "Weight"


def test_store_search_pages_never_become_offers():
    listings = [
        result("amazon.in", "Lg G3 Oled - Amazon.in", "LG 121 cm (48 inches) OLED evo TV OLED48C6PLA ₹59,990"),
        result("croma.com", "LG G3 165 cm (65 inch) OLED 4K Ultra HD WebOS TV - Croma", "Buy LG G3 online"),
        result("poorvika.com", "LG 4K Ultra HD Smart OLED TV G3 ( 77 inch ) - Poorvika", "Effective Price ₹ 4,11,100. EMI starts from ₹ 11,299/month"),
        result("croma.com", "Buy LG Ultra HD 4K OLED TV Online | Croma", ""),
    ]
    listings[0].url = "https://www.amazon.in/lg-g3-oled/s?k=lg+g3+oled"
    listings[3].url = "https://www.croma.com/l/lg-ultra-hd-4k-oled-tv-0asz00a.html"
    found = products.offers(listings, "lg g3 oled", products.Matcher("lg g3 oled"))
    assert [(o["store"], o["variant"], o["price_text"]) for o in found] == [("Poorvika", "77-inch", "₹4,11,100"), ("Croma", "65-inch", "")]
    summary = products.price_summary(found)
    assert summary["variant"] == "77-inch" and summary["low_text"] == "₹4,11,100"
    assert not products.product_page("https://www.croma.com/unboxed/sony-wh-1000xm5-price-cut", "Sony WH-1000XM5 price cut")


def test_matcher_allows_words_around_the_model_number():
    g3 = products.Matcher("lg g3 oled")
    assert g3("LG G3 164 cm (65 inch) OLED Ultra HD (4K) Smart WebOS TV") and g3("LG OLED evo G3 55 inch")
    assert not g3("LG C3 OLED 55 inch") and not g3("LG G3 164 cm (65 inch) QLED TV")


def test_photos_come_from_the_maker_and_stores_first():
    def image(host, title, res="1200x900"):
        r = result(host, title, thumb=f"https://img.{host}/t.jpg")
        r.img_src, r.resolution = f"https://img.{host}/full.jpg", res
        return r
    found = products.photos([image("jeuxvideo.com", "Soldes TV : la LG G3 OLED !"), image("rtings.com", "LG G3 OLED Review"),
                             image("amazon.in", "LG G3 OLED 65 inch"), image("lg.com", "LG G3 65-Inch OLED evo TV"),
                             image("lg.com", "LG OLED evo G3 gallery banner", "768x2421"),
                             image("amazon.in", "Remote Control for LG G3 OLED")], "LG G3 OLED", "LG")
    # Other sites only fill in after them, and only while fewer than four sold-by photos exist.
    assert [(p["source"], p["tier"]) for p in found] == [("LG", 0), ("Amazon", 1), ("RTINGS", 2), ("Jeuxvideo", 3)]
    assert found[0]["full"] == "https://img.lg.com/full.jpg" and found[1]["url"].startswith("https://amazon.in/")


def test_older_models_point_at_the_home_countrys_second_hand_market():
    old = products.secondhand("Nintendo 3DS", 2011, "IN", now=2026)
    assert old["old"] and old["stale"] and old["market"] == "OLX"
    assert old["links"] == [{"name": "OLX", "url": "https://www.olx.in/items/q-nintendo-3ds"}]
    assert products.secondhand("Nintendo 3DS", 2011, "US", now=2026)["links"][0]["url"].startswith("https://www.ebay.com/sch/")
    recent = products.secondhand("iPhone 17", 2025, "IN", now=2026)
    assert not recent["old"] and recent["links"] == []
    listings = [result("ign.com", "Nintendo 3DS review", "25 Feb 2011 ... The 3DS is here"), result("flipkart.com", "Nintendo 3DS (2011) handheld", "")]
    assert products.release_year(listings, products.Matcher("nintendo 3ds")) == 2011


def test_used_listings_stay_apart_from_new_prices():
    listings = [
        result("croma.com", "Nintendo Switch OLED - Croma", "₹32,990"),
        result("olx.in", "Nintendo Switch OLED, barely used", "₹21,000 · Bengaluru"),
        result("amazon.in", "Nintendo Switch OLED (Renewed)", "₹24,500"),
        result("swappa.com", "Nintendo Switch OLED", "$210"),                              # a US market: not for India
        result("cashify.in", "Nintendo Switch OLED Price in India", "Price ₹34,999"),      # a tracker page, not used
        result("olx.in", "Nintendo Switch OLED in Bengaluru", "₹19,000"),
    ]
    listings[1].url = "https://www.olx.in/item/nintendo-switch-oled-iid-1234"
    listings[5].url = "https://www.olx.in/en-in/games_c93/q-switch-oled"                    # a search page: dropped
    found = products.offers(listings, "nintendo switch oled", products.Matcher("nintendo switch oled"), country="IN")
    roles = {(o["store"], o["role"], o["price_text"]) for o in found}
    assert roles == {("Croma", "store", "₹32,990"), ("OLX", "used", "₹21,000"), ("Amazon", "used", "₹24,500"),
                     ("Cashify", "guide", "₹34,999")}
    assert products.price_summary(found)["low_text"] == "₹32,990"                              # used never sets the new price
    assert products.used_summary(found) == {"low_text": "₹21,000", "count": 2, "stores": ["Amazon", "OLX"]}


def test_carousel_snippets_lend_no_price():
    snippet = "SONY WH-1000XM5 Wireless... more ; 4.2 · 33% OFF. ₹14,990. ₹9,990. ₹8,091 with Bank offer ; 4.2 · 33% OFF. ₹14,990."
    found = products.offers([result("flipkart.com", "SONY WH-1000XM5 Wireless Noise Cancellation", snippet)],
                            "sony wh-1000xm5", products.Matcher("sony wh-1000xm5"))
    assert found[0]["price"] is None and found[0]["url"]


def test_trade_in_and_foreign_prices_are_not_selling_prices():
    assert products.find_price("Get $35 - $885 off an iPhone 16 when you trade in an iPhone 8 Plus or newer.") is None
    assert products.find_price("From ₹79,900 or ₹6,658/mo. for 12 mo.")["amount"] == 79900
    us = result("apple.com", "Buy iPhone 16 - Apple", "From $799 or $33.29/mo.")
    us.url = "https://www.apple.com/shop/buy-iphone/iphone-16"
    india = result("apple.com", "Buy iPhone 16 - Apple (IN)", "Get ₹4500.00 - ₹81500.00 off when you trade in.")
    india.url = "https://www.apple.com/in/shop/buy-iphone/iphone-16"
    found = products.offers([us, india], "iphone 16", products.Matcher("iphone 16"), brand="Apple", country="IN")
    assert [(o["store"], o["url"], o["price"]) for o in found] == [("Apple", india.url, None)]


def test_comparison_sides():
    assert products.comparison_sides("iPhone 16 vs iPhone 17") == ["iphone 16", "iphone 17"]
    assert products.comparison_sides("Which is better, the Galaxy S24 or the Pixel 9?") == ["galaxy s24", "pixel 9"]
    assert products.comparison_sides("Is the Sony WH-1000XM5 worth buying?") == []
    assert products.comparison_sides("is it worth it or not") == []


def test_ai_comparison_gets_a_card_for_each_product():
    import asyncio
    from types import SimpleNamespace
    from archer.ai_service import AIService
    from archer.searxng import SearchResponse

    pages = {"iphone 17": [result("flipkart.com", "Apple iPhone 17 (Black, 256 GB)", "₹82,900"),
                           result("croma.com", "Apple iPhone 17 256GB - Croma", "Now ₹82,900")]}

    class Search:
        async def search(self, q, category="general", page=1):
            return SearchResponse(results=pages.get(q.split(" price")[0].split(" review")[0].lower(), []))

    service = AIService.__new__(AIService)
    service.searx = Search()
    service.settings = SimpleNamespace(home_currency="inr", home_country="IN")
    events = []
    run = SimpleNamespace(question="iPhone 16 vs iPhone 17", ui={}, evidence=[], parent="", snapshot=SimpleNamespace(query=""),
                          emit=lambda event, data: events.append((event, data)))
    asyncio.run(service.product_card(run, SimpleNamespace(shown=set())))
    cards = [d for e, d in events if e == "card"]
    assert [c["data"]["name"] for c in cards] == ["iPhone 17"]               # iPhone 16 had no listings: no card
    assert cards[0]["data"]["price"]["low_text"] == "₹82,900"
    assert run.evidence[0].title == "Archer product card · iPhone 17" and len(run.evidence) == 3
