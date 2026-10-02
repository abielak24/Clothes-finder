"""Products, their size charts and model notes, in inches.

A product can carry any mix of three kinds of evidence about how it
fits, and the matcher uses the strongest one it has:

* a **garment** chart: what the shirt itself measures in each size;
* a **body** chart: which body each size is cut for;
* **model notes**: "model is 6'1", 40 in chest, wearing M".

Every chart and note keeps the text it was read from, so any number in
a recommendation can be traced back to the page that stated it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

GARMENT = "garment"
BODY = "body"
CHART_TYPES = (GARMENT, BODY)

MEASUREMENTS = ("chest", "waist", "length", "shoulder", "sleeve", "neck", "height")

# Letter sizes in order. Anything else that is a number (neck sizes,
# EU 48) sorts numerically after these; anything unrecognised keeps the
# order the store listed it in.
_LETTER_ORDER = [
    "XXXS", "XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "4XL", "5XL", "6XL",
]
_ALIASES = {
    "2XS": "XXS", "3XS": "XXXS", "2XL": "XXL", "3XL": "XXXL",
    "XXXXL": "4XL", "XXXXXL": "5XL",
    "SMALL": "S", "MEDIUM": "M", "LARGE": "L",
    "X-SMALL": "XS", "EXTRA SMALL": "XS", "X-LARGE": "XL", "EXTRA LARGE": "XL",
    "XX-LARGE": "XXL", "XXX-LARGE": "XXXL",
}


def normalise_size(label: str) -> str:
    """``medium`` -> ``M``, ``2xl`` -> ``XXL``; other labels unchanged."""
    cleaned = re.sub(r"\s+", " ", label.strip()).upper()
    return _ALIASES.get(cleaned, cleaned)


def size_rank(label: str) -> Optional[float]:
    """A sortable position for a size label, or None if it is not one."""
    norm = normalise_size(label)
    if norm in _LETTER_ORDER:
        return float(_LETTER_ORDER.index(norm))
    match = re.match(r"^(\d+(?:\.\d+)?)", norm)
    if match:
        return 100.0 + float(match.group(1))
    return None


def order_sizes(labels: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for label in labels:
        if label not in seen:
            seen.append(label)
    ranked = [(size_rank(s), i, s) for i, s in enumerate(seen)]
    if all(r is not None for r, _, _ in ranked):
        ranked.sort(key=lambda t: (t[0], t[1]))
    return [s for _, _, s in ranked]


@dataclass
class SizeChart:
    """One chart: for each size, a (low, high) range per measurement.

    A single published value is stored as a range of zero width.
    """

    kind: str
    sizes: Dict[str, Dict[str, Tuple[float, float]]] = field(default_factory=dict)
    source_url: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.kind not in CHART_TYPES:
            raise ValueError(f"chart kind must be garment or body, got {self.kind!r}")
        self.sizes = {
            size: {name: (float(lo), float(hi)) for name, (lo, hi) in values.items()}
            for size, values in self.sizes.items()
        }

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "sizes": {s: {n: list(r) for n, r in v.items()} for s, v in self.sizes.items()},
            "source_url": self.source_url,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SizeChart":
        return cls(
            kind=data["kind"],
            sizes={s: {n: tuple(r) for n, r in v.items()} for s, v in data["sizes"].items()},
            source_url=data.get("source_url", ""),
            evidence=data.get("evidence", ""),
        )


@dataclass
class ModelNote:
    """What a model wore, and whatever of their body the page stated."""

    size: str
    height: Optional[float] = None
    chest: Optional[float] = None
    waist: Optional[float] = None
    evidence: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ModelNote":
        return cls(**data)


@dataclass
class Product:
    url: str
    brand: str
    title: str
    sizes: List[str] = field(default_factory=list)
    product_type: str = ""
    fit_name: str = ""
    stretch: Optional[bool] = None
    charts: List[SizeChart] = field(default_factory=list)
    model_notes: List[ModelNote] = field(default_factory=list)
    price: str = ""
    fetched: str = ""
    # Set once extraction has run, whether or not it found anything, so a
    # product with no chart is not sent to Claude again on every collect.
    extracted: bool = False

    def chart(self, kind: str) -> Optional[SizeChart]:
        for chart in self.charts:
            if chart.kind == kind and chart.sizes:
                return chart
        return None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["charts"] = [c.to_dict() for c in self.charts]
        data["model_notes"] = [n.to_dict() for n in self.model_notes]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Product":
        data = dict(data)
        data["charts"] = [SizeChart.from_dict(c) for c in data.get("charts", [])]
        data["model_notes"] = [ModelNote.from_dict(n) for n in data.get("model_notes", [])]
        return cls(**data)
