"""Read size charts and model notes out of product pages, with Claude.

Product descriptions hold their sizing in every shape there is: HTML
tables, "Model is 6'1" and wears a medium", a chest in centimetres
measured flat. Writing parsers for each store would never end, so the
page text goes to Claude once, with a fixed schema to fill, and the
answer is converted to inches and checked here.

Two things are deliberately not left to the model:

* **Units.** Claude reports numbers as the page states them, plus the
  unit, and the conversion is done in code.
* **Body or garment, when the page doesn't say.** Claude may answer
  "unclear"; the chart's own numbers then decide (see
  :func:`classify_unclear`).
"""

from __future__ import annotations

import html
import json
import os
import re
from html.parser import HTMLParser
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .products import BODY, GARMENT, ModelNote, Product, SizeChart, normalise_size
from .units import to_inches

DEFAULT_MODEL = "claude-opus-5-5"

# Words that suggest a page says something about sizing. A page without
# any of them is not sent to Claude at all.
_SIZING_CUES = re.compile(
    r"\b(size|sizing|chest|bust|pit|model|measure|measurements|fit|length|shoulder|sleeve)\b",
    re.IGNORECASE,
)

# Pages are cut to this many characters. A product page's sizing is
# near its description; the long tail is navigation and reviews.
MAX_PAGE_CHARS = 40_000


# ----------------------------------------------------------------------
# Schema Claude fills in. Every field is required, with explicit
# "not stated" values instead of nulls, so the answer is always complete.
# ----------------------------------------------------------------------
Measurement = Literal["chest", "waist", "length", "shoulder", "sleeve", "neck", "height"]


class ChartCell(BaseModel):
    size: str = Field(description="Size label exactly as the chart writes it, e.g. M, Large, 15.5")
    measurement: Measurement
    low: float = Field(description="Lower bound, or the single value if only one is given")
    high: float = Field(description="Upper bound, or the same single value")


class Chart(BaseModel):
    kind: Literal["garment", "body", "unclear"] = Field(
        description="garment = measurements of the shirt itself; body = the body the size is "
        "meant for; unclear = the page does not say"
    )
    unit: Literal["in", "cm"]
    cells: List[ChartCell]
    quote: str = Field(description="The chart's heading or first line, copied from the page")


class ModelFact(BaseModel):
    size_worn: str
    height_in: float = Field(description="Model height in inches, 0 if not stated")
    chest_in: float = Field(description="Model chest in inches, 0 if not stated")
    waist_in: float = Field(description="Model waist in inches, 0 if not stated")
    quote: str = Field(description="The sentence about the model, copied from the page")


class Extraction(BaseModel):
    charts: List[Chart]
    model_notes: List[ModelFact]
    fit_name: Literal["slim", "regular", "relaxed", "oversized", "not stated"]
    stretch: Literal["yes", "no", "not stated"]
    sizes_offered: List[str] = Field(description="Sizes the product is sold in, as the page lists them")
    size_chart_url: str = Field(description="Link to a separate size-chart page, or empty")


SYSTEM_PROMPT = """You read clothing product pages and report how the product is sized.

Report only what the page states. Never fill in a typical size chart from \
general knowledge; if the page has no chart, return an empty list of charts.

Charts:
- Report each number as written, with the chart's unit. Do not convert units.
- A range such as 38-40 is low 38, high 40. A single value is low = high.
- "chest" and "waist" are full circumferences. If the chart gives a flat or \
half measurement (pit to pit, "1/2 chest", "chest width laid flat"), double it.
- "length" is the garment's body length; "sleeve" is as the chart defines it.
- kind is "garment" when the chart measures the shirt, "body" when it says which \
body each size fits, and "unclear" when the page does not say.
- Skip charts that are clearly for other products (pants, shoes, kids).

Model notes: one entry per model described ("Model is 6'1" and wears M"). \
Convert the model's height and measurements to inches; use 0 for anything \
not stated.

fit_name: the cut the page names for this product (slim, regular, relaxed, \
oversized), or "not stated". stretch: "yes" if the fabric includes elastane, \
spandex or the page says it stretches."""


# ----------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Visible text from HTML, keeping table rows on one line each."""

    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.links: List[str] = []
        self.json_ld: List[str] = []
        self._skip = 0
        self._in_json_ld = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._in_json_ld = True
            return
        if tag in self._SKIP:
            self._skip += 1
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag in ("tr", "p", "br", "li", "div", "h1", "h2", "h3", "h4", "table"):
            self.parts.append("\n")
        if tag == "a" and attrs.get("href") and "size" in (attrs.get("href") or "").lower():
            self.links.append(attrs["href"])

    def handle_endtag(self, tag):
        if self._in_json_ld and tag == "script":
            self._in_json_ld = False
        elif tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_json_ld:
            self.json_ld.append(data)
        elif not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    extra = []
    for blob in parser.json_ld:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        description = _find_description(data)
        if description:
            extra.append(html.unescape(description))
    if parser.links:
        extra.append("Size links: " + " ".join(dict.fromkeys(parser.links)))
    return "\n".join([text.strip()] + extra).strip()


def _find_description(data) -> Optional[str]:
    if isinstance(data, dict):
        if data.get("@type") in ("Product", "ProductGroup") and isinstance(data.get("description"), str):
            return data["description"]
        for value in data.values():
            found = _find_description(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_description(item)
            if found:
                return found
    return None


def mentions_sizing(text: str) -> bool:
    return bool(_SIZING_CUES.search(text))


# ----------------------------------------------------------------------
def classify_unclear(chart: SizeChart) -> str:
    """Decide body or garment from the numbers, when the page didn't say.

    A chart that lists body length or shoulder width is measuring the
    shirt. Otherwise the chest for a medium decides: men's body charts
    put a medium at about 38-40 in, while the garment itself measures
    around 4 in more.
    """
    names = {name for values in chart.sizes.values() for name in values}
    if names & {"length", "shoulder"}:
        return GARMENT
    medium = chart.sizes.get("M") or chart.sizes.get("MEDIUM")
    if medium and "chest" in medium:
        lo, hi = medium["chest"]
        return GARMENT if (lo + hi) / 2 >= 42 else BODY
    return BODY


def to_size_charts(extraction: Extraction, source_url: str) -> List[SizeChart]:
    charts: List[SizeChart] = []
    for raw in extraction.charts:
        sizes: dict = {}
        for cell in raw.cells:
            lo, hi = sorted((cell.low, cell.high))
            if lo <= 0:
                continue
            size = normalise_size(cell.size)
            sizes.setdefault(size, {})[cell.measurement] = (
                to_inches(lo, raw.unit),
                to_inches(hi, raw.unit),
            )
        if not sizes:
            continue
        evidence = raw.quote
        if raw.kind == "unclear":
            kind = classify_unclear(SizeChart(kind=BODY, sizes=sizes))
            evidence = f"{evidence} (type inferred: {kind})".strip()
        else:
            kind = raw.kind
        # A chest under 30 in is a half measurement the model failed to
        # double; no adult shirt is that size around.
        for values in sizes.values():
            for name in ("chest", "waist"):
                if name in values and values[name][1] < 30:
                    lo, hi = values[name]
                    values[name] = (lo * 2, hi * 2)
        charts.append(SizeChart(kind=kind, sizes=sizes, source_url=source_url, evidence=evidence))
    return charts


def to_model_notes(extraction: Extraction) -> List[ModelNote]:
    notes = []
    for fact in extraction.model_notes:
        if not fact.size_worn.strip():
            continue
        notes.append(
            ModelNote(
                size=normalise_size(fact.size_worn),
                height=fact.height_in or None,
                chest=fact.chest_in or None,
                waist=fact.waist_in or None,
                evidence=fact.quote,
            )
        )
    return notes


def apply_extraction(product: Product, extraction: Extraction, source_url: str) -> Product:
    product.charts.extend(to_size_charts(extraction, source_url))
    product.model_notes.extend(to_model_notes(extraction))
    if extraction.fit_name != "not stated":
        product.fit_name = extraction.fit_name
    if extraction.stretch != "not stated":
        product.stretch = extraction.stretch == "yes"
    if not product.sizes:
        product.sizes = [normalise_size(s) for s in extraction.sizes_offered if s.strip()]
    product.extracted = True
    return product


# ----------------------------------------------------------------------
class ExtractionError(RuntimeError):
    pass


def make_client():
    import anthropic

    return anthropic.Anthropic()


def extract(text: str, url: str, client=None, model: Optional[str] = None) -> Extraction:
    """Ask Claude for the sizing on one page of text."""
    import anthropic

    client = client or make_client()
    model = model or os.environ.get("FITFIND_MODEL", DEFAULT_MODEL)
    try:
        response = client.beta.messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Product page: {url}\n\n<page>\n{text[:MAX_PAGE_CHARS]}\n</page>",
                }
            ],
            # Reading a page into a schema is routine work; low effort
            # keeps it quick and cheap.
            output_config={"effort": "low"},
            output_format=Extraction,
            # If a safety classifier declines a page, rerun it on the
            # recommended fallback model instead of failing.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIStatusError as error:
        raise ExtractionError(f"Claude request failed for {url}: {error.message}") from error
    except anthropic.APIConnectionError as error:
        raise ExtractionError(f"could not reach the Claude API: {error}") from error

    if response.stop_reason == "refusal":
        raise ExtractionError(f"Claude declined to read {url}")
    if response.stop_reason == "max_tokens":
        raise ExtractionError(f"extraction for {url} was cut off")
    if response.parsed_output is None:
        raise ExtractionError(f"no structured answer for {url}")
    return response.parsed_output
