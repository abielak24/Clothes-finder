"""Collect shirts and their sizing from the brands you list.

For each store, in order, stopping at the first that works:

1. the **Shopify product feed** (``/products.json``), which many
   direct-to-consumer brands publish and which carries each product's
   description HTML, where size charts and model notes usually live;
2. otherwise the store's **sitemap**, to find product pages, which are
   fetched one by one;
3. and once per brand, the **size-chart page** a product links to, kept
   as that brand's site-wide chart.

The collector is polite by construction: at most one request per second
to a domain, ``robots.txt`` obeyed, and every response kept in the store
so a page is fetched again only once it is older than ``max_age_days``.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterator, List, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from . import __version__
from .extract import Extraction, ExtractionError, apply_extraction, html_to_text, mentions_sizing
from .products import Product, normalise_size
from .store import Page, Store

USER_AGENT = f"fitfind/{__version__} (personal size finder; one request per second)"

SHIRT_WORDS = (
    "shirt", "tee", "t-shirt", "polo", "oxford", "henley", "button-down",
    "button down", "flannel", "overshirt", "camp collar",
)
NOT_SHIRT_WORDS = ("gift card", "sock", "pant", "short", "jacket", "hat")
# Never shirts, whatever else the text says.
NEVER_SHIRTS = ("shirt dress", "shirtdress", "gift card")

Extractor = Callable[[str, str], Extraction]


def domain_of(text: str) -> str:
    text = text.strip()
    if "://" not in text:
        text = "https://" + text
    return urlparse(text).netloc.lower()


def read_brands(path) -> List[str]:
    brands = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.split("#", 1)[0].strip()
            if line:
                brands.append(domain_of(line))
    return brands


def looks_like_shirt(*texts: str) -> bool:
    joined = " ".join(t.lower() for t in texts if t)
    if any(phrase in joined for phrase in NEVER_SHIRTS):
        return False
    if any(word in joined for word in NOT_SHIRT_WORDS) and "shirt" not in joined:
        return False
    return any(word in joined for word in SHIRT_WORDS)


# ----------------------------------------------------------------------
class Fetcher:
    """HTTP GET with caching, robots.txt and a per-domain delay."""

    def __init__(
        self,
        store: Store,
        delay: float = 1.0,
        max_age_days: float = 14,
        client: Optional[httpx.Client] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.store = store
        self.delay = delay
        self.max_age = timedelta(days=max_age_days)
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
        )
        self.sleep = sleep
        self._last: Dict[str, float] = {}
        self._robots: Dict[str, Optional[RobotFileParser]] = {}

    def _fresh(self, page: Page) -> bool:
        fetched = datetime.fromisoformat(page.fetched)
        return datetime.now(timezone.utc) - fetched < self.max_age

    def _wait(self, domain: str) -> None:
        last = self._last.get(domain)
        if last is not None:
            remaining = self.delay - (time.monotonic() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last[domain] = time.monotonic()

    def allowed(self, url: str) -> bool:
        domain = urlparse(url).netloc
        if domain not in self._robots:
            parser: Optional[RobotFileParser] = RobotFileParser()
            robots_url = f"https://{domain}/robots.txt"
            try:
                self._wait(domain)
                response = self.client.get(robots_url)
                if response.status_code >= 400:
                    parser = None  # no robots.txt: everything allowed
                else:
                    parser.parse(response.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[domain] = parser
        parser = self._robots[domain]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def get(self, url: str, refresh: bool = False) -> Optional[Page]:
        """The page at ``url``, from the store if fresh, else fetched.

        Returns None when robots.txt disallows the URL or the request
        fails outright. HTTP errors are returned as pages with their
        status, so callers can tell "not found" from "not tried".
        """
        cached = self.store.page(url)
        if cached and not refresh and self._fresh(cached):
            return cached
        if not self.allowed(url):
            return None
        self._wait(urlparse(url).netloc)
        try:
            response = self.client.get(url)
        except httpx.HTTPError:
            return None
        return self.store.save_page(
            url, response.status_code, response.headers.get("content-type", ""), response.text
        )


# ----------------------------------------------------------------------
def shopify_feed(fetcher: Fetcher, domain: str, max_pages: int = 20) -> Optional[List[dict]]:
    """All products from a Shopify store's feed, or None if it has none."""
    products: List[dict] = []
    for page_number in range(1, max_pages + 1):
        page = fetcher.get(f"https://{domain}/products.json?limit=250&page={page_number}")
        if page is None or page.status != 200:
            break
        try:
            batch = json.loads(page.body).get("products")
        except (ValueError, AttributeError):
            batch = None
        if batch is None:
            break
        if not batch:
            break
        products.extend(batch)
    return products if products else None


def _tags(data: dict) -> str:
    tags = data.get("tags") or []
    return " ".join(tags) if isinstance(tags, list) else str(tags)


def product_from_shopify(domain: str, data: dict) -> Product:
    sizes: List[str] = []
    for option in data.get("options") or []:
        if str(option.get("name", "")).strip().lower() in ("size", "sizes"):
            sizes = [normalise_size(str(v)) for v in option.get("values") or []]
    variants = data.get("variants") or []
    return Product(
        url=f"https://{domain}/products/{data.get('handle', '')}",
        brand=domain,
        title=str(data.get("title", "")),
        sizes=sizes,
        product_type=str(data.get("product_type", "") or _tags(data)),
        price=str(variants[0].get("price", "")) if variants else "",
        fetched=datetime.now(timezone.utc).date().isoformat(),
    )


def sitemap_product_urls(fetcher: Fetcher, domain: str, limit: int) -> List[str]:
    """Product page URLs from a store's sitemap that look like shirts."""
    found: List[str] = []
    queue = [f"https://{domain}/sitemap.xml"]
    seen = set()
    while queue and len(found) < limit:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        page = fetcher.get(url)
        if page is None or page.status != 200:
            continue
        try:
            root = ET.fromstring(page.body.encode("utf-8"))
        except ET.ParseError:
            continue
        for loc in root.iter():
            if not loc.tag.endswith("loc") or not loc.text:
                continue
            link = loc.text.strip()
            if root.tag.endswith("sitemapindex"):
                # Follow product sitemaps first; skip blogs, pages, images.
                if "product" in link.lower():
                    queue.insert(0, link)
                continue
            path = urlparse(link).path.lower()
            if looks_like_shirt(path.replace("-", " ")) and link not in found:
                found.append(link)
                if len(found) >= limit:
                    break
    return found


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def product_from_page(domain: str, url: str, page: Page) -> Product:
    match = _TITLE.search(page.body)
    title = re.sub(r"\s+", " ", match.group(1)).strip() if match else url
    return Product(
        url=url,
        brand=domain,
        title=title,
        fetched=datetime.now(timezone.utc).date().isoformat(),
    )


# ----------------------------------------------------------------------
@dataclass
class CollectReport:
    brand: str
    source: str = ""
    seen: int = 0
    shirts: int = 0
    extracted: int = 0
    with_sizing: int = 0
    brand_chart: bool = False
    errors: List[str] = field(default_factory=list)


def _extract_into(
    product: Product, text: str, source_url: str, extractor: Extractor, report: CollectReport
) -> Optional[Extraction]:
    if not mentions_sizing(text):
        product.extracted = True
        return None
    try:
        extraction = extractor(text, source_url)
    except ExtractionError as error:
        report.errors.append(str(error))
        return None
    apply_extraction(product, extraction, source_url)
    report.extracted += 1
    if product.charts or product.model_notes:
        report.with_sizing += 1
    return extraction


def _brand_chart_from(
    link: str, domain: str, fetcher: Fetcher, store: Store, extractor: Extractor, report: CollectReport
) -> None:
    url = urljoin(f"https://{domain}/", link)
    if domain_of(url) != domain:
        return
    page = fetcher.get(url)
    if page is None or page.status != 200:
        return
    holder = Product(url=url, brand=domain, title="size chart")
    # A separate report, so the chart page isn't counted as a product.
    scratch = CollectReport(brand=domain)
    _extract_into(holder, html_to_text(page.body), url, extractor, scratch)
    report.errors.extend(scratch.errors)
    for chart in holder.charts:
        store.save_brand_chart(domain, chart)
        report.brand_chart = True
        return


def collect_brand(
    domain: str,
    store: Store,
    fetcher: Fetcher,
    extractor: Extractor,
    limit: int = 200,
    reextract: bool = False,
    progress: Callable[[str], None] = lambda message: None,
) -> CollectReport:
    report = CollectReport(brand=domain)
    feed = shopify_feed(fetcher, domain)
    candidates: Iterator = iter(())
    if feed is not None:
        report.source = "shopify feed"
        report.seen = len(feed)
        shirts = [
            item for item in feed
            if looks_like_shirt(str(item.get("product_type", "")), str(item.get("title", "")), _tags(item))
        ][:limit]
        candidates = (("shopify", item) for item in shirts)
    else:
        urls = sitemap_product_urls(fetcher, domain, limit)
        report.source = "sitemap" if urls else "nothing found"
        report.seen = len(urls)
        candidates = (("page", url) for url in urls)

    want_brand_chart = store.brand_chart(domain) is None
    for kind, item in candidates:
        if kind == "shopify":
            product = product_from_shopify(domain, item)
            text = html_to_text(str(item.get("body_html") or ""))
            source_url = product.url
        else:
            page = fetcher.get(item)
            if page is None or page.status != 200:
                report.errors.append(f"could not fetch {item}")
                continue
            product = product_from_page(domain, item, page)
            text = html_to_text(page.body)
            source_url = item

        report.shirts += 1
        existing = store.product(product.url)
        if existing and existing.extracted and not reextract:
            # Keep what was extracted; refresh what the feed says now.
            existing.sizes = product.sizes or existing.sizes
            existing.price = product.price or existing.price
            store.save_product(existing)
            if existing.charts or existing.model_notes:
                report.with_sizing += 1
            continue

        progress(f"{domain}: reading {product.title}")
        extraction = _extract_into(product, text, source_url, extractor, report)
        store.save_product(product)

        link = extraction.size_chart_url if extraction else ""
        if want_brand_chart and link:
            _brand_chart_from(link, domain, fetcher, store, extractor, report)
            want_brand_chart = not report.brand_chart
    return report


def check_url(url: str, store: Store, fetcher: Fetcher, extractor: Extractor) -> Optional[Product]:
    """Fetch and read a single product, for ``fitfind check``."""
    domain = domain_of(url)
    url = url.split("#", 1)[0]
    base = url.split("?", 1)[0].rstrip("/")
    report = CollectReport(brand=domain)

    product: Optional[Product] = None
    text = ""
    if "/products/" in base:
        page = fetcher.get(base + ".json")
        if page is not None and page.status == 200:
            try:
                data = json.loads(page.body).get("product")
            except (ValueError, AttributeError):
                data = None
            if data:
                product = product_from_shopify(domain, data)
                text = html_to_text(str(data.get("body_html") or ""))
    if product is None:
        page = fetcher.get(url)
        if page is None or page.status != 200:
            return None
        product = product_from_page(domain, url, page)
        text = html_to_text(page.body)

    existing = store.product(product.url)
    if existing and existing.extracted:
        return existing
    extraction = _extract_into(product, text, product.url, extractor, report)
    if report.errors:
        raise ExtractionError(report.errors[0])
    store.save_product(product)
    if extraction and extraction.size_chart_url and store.brand_chart(domain) is None:
        _brand_chart_from(extraction.size_chart_url, domain, fetcher, store, extractor, report)
    return product
