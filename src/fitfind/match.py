"""Score every size of a product against your profile.

Each size gets a fit score from 0 to 1. For each measurement both sides
know, the difference ``d`` is taken (garment side minus yours, so a
positive ``d`` means room to spare and a negative one means too tight),
and scores 1 at zero falling to 0 at that measurement's tolerance:

    score = sum_i w_i * max(0, 1 - |d_i| / t_i)      (weights renormalised)

Garment differences within half an inch count as exact: shirts of one
size vary by about that much off the line, and you can't feel it. The
score then falls from 1 at half an inch to 0 at the tolerance.

A size that is too tight reaches zero sooner than one the same amount
too loose, by ``Profile.tight_penalty``, because a loose shirt can still
be worn. Stretch fabric removes that asymmetry.

The product's own evidence is used in order of strength: a garment
chart, then a body chart, then model notes, then a chart published for
the whole brand. Only one is used per product, and the result says
which, so a recommendation is never a blend that cannot be checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .products import BODY, GARMENT, Product, SizeChart, order_sizes, size_rank
from .profile import EASE, Profile

# Inches at which a difference scores zero (before the tight penalty).
GARMENT_DEADBAND = 0.5
GARMENT_TOLERANCE = {"chest": 1.5, "waist": 1.5, "length": 1.5, "shoulder": 1.0, "sleeve": 1.0}
BODY_TOLERANCE = {"chest": 2.0, "waist": 2.0, "neck": 0.75, "sleeve": 1.0, "height": 3.0}

GARMENT_WEIGHTS = {"chest": 0.45, "length": 0.25, "shoulder": 0.15, "sleeve": 0.10, "waist": 0.05}
BODY_WEIGHTS = {"chest": 0.50, "waist": 0.20, "neck": 0.10, "sleeve": 0.10, "height": 0.10}

# Typical step between adjacent letter sizes, used to read model notes.
CHEST_STEP = 2.0
HEIGHT_STEP = 1.5

CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2, "very low": 3}

# When ranking products against each other, a score is discounted by how
# much its evidence can be trusted, so a guess from model notes doesn't
# outrank a product whose garment chart was measured against your shirt.
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.9, "low": 0.75, "very low": 0.6}

# Scores closer than this are reported as "between" the two sizes.
CLOSE_CALL = 0.05


@dataclass
class SizeScore:
    size: str
    score: float
    diffs: Dict[str, float] = field(default_factory=dict)


@dataclass
class Recommendation:
    product: Product
    size: Optional[str]
    score: float
    method: str
    confidence: str
    reason: str
    runner_up: Optional[str] = None
    sizes: List[SizeScore] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.size is None:
            return "-"
        if self.runner_up:
            ordered = order_sizes([self.size, self.runner_up])
            return "/".join(ordered)
        return self.size


# ----------------------------------------------------------------------
def _measurement_score(d: float, tolerance: float, tight_penalty: float, deadband: float = 0.0) -> float:
    span = tolerance - deadband
    if d < 0:
        span = span / tight_penalty
    return max(0.0, 1.0 - max(0.0, abs(d) - deadband) / span)


def _weighted(parts: Dict[str, float], weights: Dict[str, float], priority: str) -> float:
    total_weight = 0.0
    total = 0.0
    for name, value in parts.items():
        weight = weights.get(name, 0.05)
        if name == priority:
            weight *= 2
        total_weight += weight
        total += weight * value
    return total / total_weight if total_weight else 0.0


def _range_diff(lo: float, hi: float, value: float) -> float:
    """How far a range sits from a value: 0 inside it, signed outside.

    Positive when the range is above the value (the size is cut for a
    bigger body, so it is loose on you), negative when below (tight).
    """
    if value < lo:
        return lo - value
    if value > hi:
        return hi - value
    return 0.0


def _centre_offset(lo: float, hi: float, value: float) -> float:
    return (lo + hi) / 2 - value


# ----------------------------------------------------------------------
def score_garment_chart(
    chart: SizeChart, profile: Profile, bias: float = 0.0, stretch: bool = False
) -> Tuple[List[SizeScore], List[str]]:
    targets = profile.garment_targets()
    penalty = 1.0 if stretch else profile.tight_penalty
    scores: List[SizeScore] = []
    used: List[str] = []
    for size, values in chart.sizes.items():
        parts: Dict[str, float] = {}
        diffs: Dict[str, float] = {}
        for name, (lo, hi) in values.items():
            if name not in targets or name not in GARMENT_TOLERANCE:
                continue
            target = targets[name] + (bias if name in ("chest", "waist") else 0.0)
            d = (lo + hi) / 2 - target
            diffs[name] = d
            parts[name] = _measurement_score(d, GARMENT_TOLERANCE[name], penalty, GARMENT_DEADBAND)
            if name not in used:
                used.append(name)
        if parts:
            scores.append(SizeScore(size, _weighted(parts, GARMENT_WEIGHTS, profile.priority), diffs))
    return scores, used


def _body_values(profile: Profile) -> Tuple[Dict[str, float], bool]:
    """Body measurements, estimated from the reference shirt if needed.

    Returns the values and whether any were estimated.
    """
    values = dict(profile.body)
    estimated = False
    ease = EASE[profile.fit]
    for name in ("chest", "waist"):
        if name not in values and name in profile.shirt:
            values[name] = profile.shirt[name] - ease[name]
            estimated = True
    return values, estimated


def score_body_chart(
    chart: SizeChart, profile: Profile, bias: float = 0.0, stretch: bool = False
) -> Tuple[List[SizeScore], List[str], bool]:
    body, estimated = _body_values(profile)
    penalty = 1.0 if stretch else profile.tight_penalty
    scores: List[SizeScore] = []
    used: List[str] = []
    for size, values in chart.sizes.items():
        parts: Dict[str, float] = {}
        diffs: Dict[str, float] = {}
        for name, (lo, hi) in values.items():
            if name not in body or name not in BODY_TOLERANCE:
                continue
            you = body[name] + (bias if name in ("chest", "waist") else 0.0)
            d = _range_diff(lo, hi, you)
            diffs[name] = d
            parts[name] = _measurement_score(d, BODY_TOLERANCE[name], penalty)
            # Keep where you sit inside the range, for breaking ties.
            diffs[f"_{name}_centre"] = _centre_offset(lo, hi, you)
            if name not in used:
                used.append(name)
        if parts:
            scores.append(SizeScore(size, _weighted(parts, BODY_WEIGHTS, profile.priority), diffs))
    return scores, used, estimated


def chart_from_model_notes(product: Product) -> Optional[SizeChart]:
    """Turn "model has a 40 in chest and wears M" into a body chart.

    The size the model wears is taken to be cut for the model's body,
    and each size either side for a body one step bigger or smaller.
    That needs sizes that can be put in order; numeric or unknown size
    labels produce no chart.
    """
    notes = [n for n in product.model_notes if n.chest or n.height]
    if not notes:
        return None
    sizes = order_sizes(list(product.sizes) + [n.size for n in notes])
    if any(size_rank(s) is None for s in sizes):
        return None
    position = {s: i for i, s in enumerate(sizes)}
    chart: Dict[str, Dict[str, Tuple[float, float]]] = {s: {} for s in sizes}
    evidence = []
    note = notes[0]
    # When several notes disagree, use the one that states a chest:
    # it is the measurement shirts are sized by.
    for candidate in notes:
        if candidate.chest:
            note = candidate
            break
    worn = position[note.size]
    for size, i in position.items():
        steps = i - worn
        if note.chest:
            centre = note.chest + steps * CHEST_STEP
            chart[size]["chest"] = (centre - CHEST_STEP / 2, centre + CHEST_STEP / 2)
        elif note.height:
            centre = note.height + steps * HEIGHT_STEP
            chart[size]["height"] = (centre - HEIGHT_STEP, centre + HEIGHT_STEP)
    evidence.append(note.evidence)
    return SizeChart(kind=BODY, sizes=chart, source_url=product.url, evidence=" | ".join(evidence))


def _pick(scores: List[SizeScore], fit: str) -> Tuple[SizeScore, Optional[SizeScore]]:
    """The best size, and a runner-up when the call is close.

    Exact ties go to the smaller size for a slim fit and the larger for
    a relaxed one. For a regular fit they go to the size whose range is
    centred nearest you, and failing that to the larger, since a shirt
    with room to spare can still be worn.
    """
    def rank(s: SizeScore) -> float:
        return size_rank(s.size) if size_rank(s.size) is not None else 0.0

    def tie_key(s: SizeScore) -> Tuple[float, float]:
        if fit == "slim":
            return (rank(s), 0.0)
        if fit == "relaxed":
            return (-rank(s), 0.0)
        centre = s.diffs.get("_chest_centre")
        return (round(abs(centre), 3) if centre is not None else 0.0, -rank(s))

    ordered = sorted(scores, key=lambda s: (-round(s.score, 3), tie_key(s)))
    best = ordered[0]
    runner = None
    if len(ordered) > 1 and best.score - ordered[1].score < CLOSE_CALL and best.score > 0:
        candidate = ordered[1]
        ranks = sorted(rank(s) for s in (best, candidate))
        if size_rank(best.size) is not None and size_rank(candidate.size) is not None and ranks[1] - ranks[0] == 1:
            runner = candidate
    return best, runner


def _describe(best: SizeScore, sources: Dict[str, str]) -> str:
    parts = []
    for name, d in sorted(best.diffs.items(), key=lambda kv: -abs(kv[1])):
        if name.startswith("_"):
            continue
        if abs(d) < 0.05:
            parts.append(f"{name} spot on")
        else:
            parts.append(f"{name} {d:+.1f} in")
    detail = ", ".join(parts[:3]) if parts else "no measurements in common"
    src = sources.get("chest") or next(iter(sources.values()), "")
    return f"{detail} vs {src}" if src else detail


# ----------------------------------------------------------------------
def recommend(
    product: Product,
    profile: Profile,
    brand_chart: Optional[SizeChart] = None,
    bias: float = 0.0,
) -> Recommendation:
    """Recommend a size for one product.

    ``bias`` (inches) comes from feedback on shirts you own from the same
    brand: positive if the brand has run small on you, which makes the
    matcher treat you as that much bigger.
    """
    stretch = bool(product.stretch)
    attempts = []

    garment = product.chart(GARMENT)
    if garment:
        attempts.append(("garment chart", garment, "high" if profile.shirt else "medium"))
    body = product.chart(BODY)
    if body:
        attempts.append(("body chart", body, "medium"))
    notes = chart_from_model_notes(product)
    if notes:
        attempts.append(("model notes", notes, "low"))
    if brand_chart and brand_chart.sizes:
        attempts.append((f"brand-wide {brand_chart.kind} chart", brand_chart, "very low"))

    for method, chart, confidence in attempts:
        if chart.kind == GARMENT:
            scores, used = score_garment_chart(chart, profile, bias, stretch)
            sources = {name: profile.target_source(name) for name in used}
        else:
            scores, used, estimated = score_body_chart(chart, profile, bias, stretch)
            label = "your body (estimated from your shirt)" if estimated else "your body"
            if method == "model notes":
                label = f"the model ({chart.evidence.strip()})" if chart.evidence else "the model"
            sources = {name: label for name in used}
            if estimated and confidence == "medium":
                confidence = "low"
        if not scores:
            continue
        best, runner = _pick(scores, profile.fit)
        reason = f"{method}: {_describe(best, sources)}"
        if runner:
            reason += f"; {runner.size} scores {runner.score:.2f}"
        if bias:
            reason += f"; adjusted {bias:+.1f} in for how this brand fit you"
        return Recommendation(
            product=product,
            size=best.size,
            score=best.score,
            method=method,
            confidence=confidence,
            reason=reason,
            runner_up=runner.size if runner else None,
            sizes=sorted(scores, key=lambda s: size_rank(s.size) if size_rank(s.size) is not None else 0.0),
        )

    return Recommendation(
        product=product,
        size=None,
        score=0.0,
        method="none",
        confidence="none",
        reason="no size chart, model notes or measurements in common with your profile",
    )


def rank(recommendations: Sequence[Recommendation]) -> List[Recommendation]:
    usable = [r for r in recommendations if r.size is not None]
    return sorted(
        usable,
        key=lambda r: (-r.score * CONFIDENCE_WEIGHT.get(r.confidence, 0.5), CONFIDENCE_ORDER.get(r.confidence, 9)),
    )
