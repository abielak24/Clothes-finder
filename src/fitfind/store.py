"""One SQLite file for products, raw pages and your feedback.

Raw pages are kept as fetched, so extraction can be rerun after the
prompt or schema changes without asking any store for the page again.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from .products import Product, SizeChart

FITS = ("good", "tight", "loose")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    fetched TEXT NOT NULL,
    status INTEGER NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    url TEXT PRIMARY KEY,
    brand TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS products_brand ON products(brand);
CREATE TABLE IF NOT EXISTS brand_charts (
    brand TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS owned (
    url TEXT NOT NULL,
    brand TEXT NOT NULL,
    title TEXT NOT NULL,
    size TEXT NOT NULL,
    fit TEXT NOT NULL,
    noted TEXT NOT NULL,
    PRIMARY KEY (url, size)
);
"""


def default_db_path() -> Path:
    return Path.home() / ".fitfind" / "fitfind.db"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Page:
    url: str
    fetched: str
    status: int
    content_type: str
    body: str


@dataclass
class Owned:
    url: str
    brand: str
    title: str
    size: str
    fit: str
    noted: str


class Store:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or default_db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- pages ---------------------------------------------------------
    def page(self, url: str) -> Optional[Page]:
        row = self.db.execute(
            "SELECT url, fetched, status, content_type, body FROM pages WHERE url = ?", (url,)
        ).fetchone()
        return Page(*row) if row else None

    def save_page(self, url: str, status: int, content_type: str, body: str) -> Page:
        page = Page(url, now(), status, content_type, body)
        self.db.execute(
            "INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?)",
            (page.url, page.fetched, page.status, page.content_type, page.body),
        )
        self.db.commit()
        return page

    # -- products ------------------------------------------------------
    def save_product(self, product: Product) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO products VALUES (?, ?, ?)",
            (product.url, product.brand, json.dumps(product.to_dict())),
        )
        self.db.commit()

    def product(self, url: str) -> Optional[Product]:
        row = self.db.execute("SELECT data FROM products WHERE url = ?", (url,)).fetchone()
        return Product.from_dict(json.loads(row[0])) if row else None

    def products(self, brand: Optional[str] = None) -> Iterator[Product]:
        if brand:
            rows = self.db.execute("SELECT data FROM products WHERE brand = ? ORDER BY url", (brand,))
        else:
            rows = self.db.execute("SELECT data FROM products ORDER BY brand, url")
        for (data,) in rows:
            yield Product.from_dict(json.loads(data))

    def brands(self) -> List[str]:
        return [b for (b,) in self.db.execute("SELECT DISTINCT brand FROM products ORDER BY brand")]

    # -- brand-wide charts ---------------------------------------------
    def save_brand_chart(self, brand: str, chart: SizeChart) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO brand_charts VALUES (?, ?)", (brand, json.dumps(chart.to_dict()))
        )
        self.db.commit()

    def brand_chart(self, brand: str) -> Optional[SizeChart]:
        row = self.db.execute("SELECT data FROM brand_charts WHERE brand = ?", (brand,)).fetchone()
        return SizeChart.from_dict(json.loads(row[0])) if row else None

    # -- shirts you own ------------------------------------------------
    def save_owned(self, product: Product, size: str, fit: str) -> Owned:
        if fit not in FITS:
            raise ValueError(f"fit must be one of {', '.join(FITS)}")
        owned = Owned(product.url, product.brand, product.title, size, fit, now())
        self.db.execute(
            "INSERT OR REPLACE INTO owned VALUES (?, ?, ?, ?, ?, ?)",
            (owned.url, owned.brand, owned.title, owned.size, owned.fit, owned.noted),
        )
        self.db.commit()
        return owned

    def owned(self) -> List[Owned]:
        rows = self.db.execute(
            "SELECT url, brand, title, size, fit, noted FROM owned ORDER BY noted DESC"
        )
        return [Owned(*row) for row in rows]

    def brand_bias(self) -> Dict[str, float]:
        """Inches to adjust you by, per brand, from shirts you've rated.

        Each "tight" counts +1 in (the brand runs small, so treat you as
        bigger), each "loose" -1 in, each "good" 0, averaged per brand.
        One inch is about half a size step: enough to tip a close call,
        not enough to override a clear one.
        """
        totals: Dict[str, List[float]] = {}
        for item in self.owned():
            totals.setdefault(item.brand, []).append({"tight": 1.0, "loose": -1.0}.get(item.fit, 0.0))
        return {brand: sum(v) / len(v) for brand, v in totals.items() if any(v)}
