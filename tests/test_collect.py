"""Collecting from stores, against fake HTTP responses."""

from __future__ import annotations

import json

import httpx
import pytest

from fitfind.collect import Fetcher, check_url, collect_brand, looks_like_shirt, read_brands
from fitfind.extract import Chart, ChartCell, Extraction, ModelFact
from fitfind.products import BODY
from fitfind.store import Store


def answer(chest=None, model=None, link=""):
    charts = []
    if chest:
        charts.append(Chart(kind="body", unit="in", quote="Size guide",
                            cells=[ChartCell(size=s, measurement="chest", low=lo, high=hi)
                                   for s, (lo, hi) in chest.items()]))
    notes = [ModelFact(size_worn=model, height_in=73, chest_in=40, waist_in=0, quote="Model wears " + model)] if model else []
    return Extraction(charts=charts, model_notes=notes, fit_name="not stated", stretch="not stated",
                      sizes_offered=[], size_chart_url=link)


class FakeExtractor:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, text, url):
        self.calls.append(url)
        for key, value in self.answers.items():
            if key in url:
                return value
        return answer()


def shopify_product(handle, title, product_type="Shirts", body="<p>Model wears M. Chest fits 38-40.</p>"):
    return {
        "handle": handle, "title": title, "product_type": product_type, "tags": [],
        "body_html": body,
        "options": [{"name": "Size", "values": ["S", "M", "L"]}],
        "variants": [{"price": "68.00"}],
    }


def make_fetcher(store, routes, requests):
    def handler(request):
        requests.append(str(request.url))
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        if key in routes:
            body, status = routes[key]
            return httpx.Response(status, text=body)
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(store, delay=0, client=client, sleep=lambda s: None)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "f.db")


def test_shopify_feed_is_read_and_filtered(store):
    feed = {"products": [
        shopify_product("oxford", "Oxford Shirt"),
        shopify_product("socks", "Wool Socks", product_type="Socks"),
    ]}
    routes = {
        "/robots.txt": ("User-agent: *\nDisallow: /cart", 200),
        "/products.json?limit=250&page=1": (json.dumps(feed), 200),
        "/products.json?limit=250&page=2": (json.dumps({"products": []}), 200),
    }
    requests = []
    extractor = FakeExtractor({"oxford": answer(chest={"M": (38, 40)})})
    report = collect_brand("acme.test", store, make_fetcher(store, routes, requests), extractor)
    assert report.source == "shopify feed"
    assert (report.seen, report.shirts, report.extracted, report.with_sizing) == (2, 1, 1, 1)
    product = store.product("https://acme.test/products/oxford")
    assert product.sizes == ["S", "M", "L"]
    assert product.price == "68.00"
    assert product.chart(BODY).sizes["M"]["chest"] == (38, 40)


def test_extracted_products_are_not_sent_to_claude_again(store):
    feed = {"products": [shopify_product("oxford", "Oxford Shirt")]}
    routes = {
        "/products.json?limit=250&page=1": (json.dumps(feed), 200),
        "/products.json?limit=250&page=2": (json.dumps({"products": []}), 200),
    }
    extractor = FakeExtractor({})
    fetcher = make_fetcher(store, routes, [])
    collect_brand("acme.test", store, fetcher, extractor)
    collect_brand("acme.test", store, fetcher, extractor)
    assert len(extractor.calls) == 1
    collect_brand("acme.test", store, fetcher, extractor, reextract=True)
    assert len(extractor.calls) == 2


def test_pages_are_cached(store):
    routes = {"/products.json?limit=250&page=1": (json.dumps({"products": []}), 200)}
    requests = []
    fetcher = make_fetcher(store, routes, requests)
    fetcher.get("https://acme.test/products.json?limit=250&page=1")
    fetcher.get("https://acme.test/products.json?limit=250&page=1")
    assert requests.count("https://acme.test/products.json?limit=250&page=1") == 1


def test_robots_txt_is_obeyed(store):
    routes = {"/robots.txt": ("User-agent: *\nDisallow: /", 200)}
    requests = []
    fetcher = make_fetcher(store, routes, requests)
    assert fetcher.get("https://acme.test/products/x") is None
    assert requests == ["https://acme.test/robots.txt"]


def test_sitemap_fallback_and_brand_chart(store):
    index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://shop.test/sitemap_products.xml</loc></sitemap>
      <sitemap><loc>https://shop.test/sitemap_blog.xml</loc></sitemap></sitemapindex>"""
    products = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://shop.test/p/linen-shirt</loc></url>
      <url><loc>https://shop.test/p/canvas-tote</loc></url></urlset>"""
    page = "<html><title>Linen Shirt | Shop</title><body>Model wears M <a href='/size-guide'>Size guide</a></body></html>"
    routes = {
        "/sitemap.xml": (index, 200),
        "/sitemap_products.xml": (products, 200),
        "/p/linen-shirt": (page, 200),
        "/size-guide": ("<table><tr><td>M</td><td>38-40</td></tr></table> chest", 200),
    }
    requests = []
    extractor = FakeExtractor({
        "linen-shirt": answer(model="M", link="/size-guide"),
        "size-guide": answer(chest={"M": (38, 40), "L": (41, 43)}),
    })
    report = collect_brand("shop.test", store, make_fetcher(store, routes, requests), extractor)
    assert report.source == "sitemap"
    assert report.shirts == 1
    assert report.brand_chart
    assert "https://shop.test/sitemap_blog.xml" not in requests
    product = store.product("https://shop.test/p/linen-shirt")
    assert product.title == "Linen Shirt | Shop"
    assert product.model_notes[0].size == "M"
    assert store.brand_chart("shop.test").sizes["L"]["chest"] == (41, 43)


def test_check_url_uses_the_shopify_product_json(store):
    data = {"product": shopify_product("oxford", "Oxford Shirt")}
    routes = {"/products/oxford.json": (json.dumps(data), 200)}
    extractor = FakeExtractor({"oxford": answer(chest={"M": (38, 40)})})
    product = check_url("https://acme.test/products/oxford?variant=1", store,
                        make_fetcher(store, routes, []), extractor)
    assert product.title == "Oxford Shirt"
    assert product.chart(BODY)


def test_read_brands(tmp_path):
    path = tmp_path / "brands.txt"
    path.write_text("# mine\nhttps://www.Acme.test/collections/all\nshop.test  # notes\n\n")
    assert read_brands(path) == ["www.acme.test", "shop.test"]


@pytest.mark.parametrize(
    "text, expected",
    [("Oxford Shirt", True), ("Pocket Tee", True), ("Shirt Dress", False), ("Short Sleeve Shirt", True),
     ("Chino Pant", False), ("Gift Card", False), ("Rain Jacket", False)],
)
def test_looks_like_shirt(text, expected):
    assert looks_like_shirt(text) is expected
