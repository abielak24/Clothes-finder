"""Your measurements, entered once and kept in a JSON file.

There are two sets, because size charts come in two kinds:

* **body** measurements, taken with a tape, compared against charts that
  say which body a size is cut for ("fits chest 38-40 in");
* **reference shirt** measurements, taken from a shirt that already fits
  you well, laid flat, compared against charts that measure the garment.

The reference shirt is the better of the two. It already contains the
amount of room you like, so a product whose garment chest is within half
an inch of it will feel the same on. Body measurements need an allowance
for ease added on top, and that allowance is a guess until feedback from
shirts you own narrows it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

FITS = ("slim", "regular", "relaxed")

# Measurements a profile may hold, by set. Chest on the reference shirt
# is the full circumference: double the pit-to-pit width.
BODY_MEASUREMENTS = ("chest", "waist", "neck", "sleeve", "height")
SHIRT_MEASUREMENTS = ("chest", "length", "shoulder", "sleeve")

# Extra garment room over the body, in inches, for each preferred fit.
# Rough starting values from common pattern-making allowances. They are
# used only when a garment chart has to be compared with body
# measurements because no reference shirt was entered.
EASE = {
    "slim": {"chest": 2.5, "waist": 2.5},
    "regular": {"chest": 4.5, "waist": 4.5},
    "relaxed": {"chest": 7.0, "waist": 7.0},
}


def default_profile_path() -> Path:
    return Path.home() / ".fitfind" / "profile.json"


@dataclass
class Profile:
    body: Dict[str, float] = field(default_factory=dict)
    shirt: Dict[str, float] = field(default_factory=dict)
    fit: str = "regular"
    # The measurement that wins when sizes disagree, e.g. a medium fits the
    # chest but runs short while a large fits the length but is boxy.
    priority: str = "chest"
    # How much more a too-tight size is penalised than a too-loose one.
    # 1.0 treats them equally; 1.5 means an inch too tight costs as much
    # as an inch and a half too loose.
    tight_penalty: float = 1.5

    def __post_init__(self) -> None:
        if self.fit not in FITS:
            raise ValueError(f"fit must be one of {', '.join(FITS)}, got {self.fit!r}")
        for name in self.body:
            if name not in BODY_MEASUREMENTS:
                raise ValueError(f"unknown body measurement {name!r}")
        for name in self.shirt:
            if name not in SHIRT_MEASUREMENTS:
                raise ValueError(f"unknown reference-shirt measurement {name!r}")

    @property
    def empty(self) -> bool:
        return not self.body and not self.shirt

    def garment_targets(self) -> Dict[str, float]:
        """What a well-fitting garment should measure, by measurement.

        The reference shirt is used as it stands. Where it is missing a
        measurement, body measurements plus the ease for your fit fill
        in, so a profile with only a tape-measured chest can still be
        compared with garment charts.
        """
        targets: Dict[str, float] = {}
        ease = EASE[self.fit]
        # Body sleeve is measured from the centre of the back, garment
        # sleeve from the shoulder seam, so the two are never mixed.
        for name, value in self.body.items():
            if name in ease:
                targets[name] = value + ease[name]
        targets.update(self.shirt)
        return targets

    def target_source(self, name: str) -> str:
        if name in self.shirt:
            return "your reference shirt"
        return f"your body + {self.fit} ease"

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        return cls(
            body={k: float(v) for k, v in (data.get("body") or {}).items()},
            shirt={k: float(v) for k, v in (data.get("shirt") or {}).items()},
            fit=data.get("fit", "regular"),
            priority=data.get("priority", "chest"),
            tight_penalty=float(data.get("tight_penalty", 1.5)),
        )

    def save(self, path: Optional[Path] = None) -> Path:
        path = Path(path or default_profile_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Profile":
        path = Path(path or default_profile_path())
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
