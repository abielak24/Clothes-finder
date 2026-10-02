"""Command-line interface.

Subcommands:

``profile``  set or show your measurements and fit preference
``collect``  read shirts and their sizing from the brands you list
``rank``     rank collected shirts by how well their best size fits you
``check``    size one product from its URL
``show``     print what was collected for one product
``owned``    record how a shirt you bought fits, or list those records
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .collect import Fetcher, check_url, collect_brand, domain_of, read_brands
from .extract import ExtractionError, extract
from .match import CONFIDENCE_ORDER, Recommendation, rank, recommend
from .products import normalise_size
from .profile import FITS, Profile
from .store import FITS as OWNED_FITS
from .store import Store
from .units import format_height, format_inches, parse_length

CONFIDENCES = ("high", "medium", "low", "very low")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _store(args) -> Store:
    return Store(Path(args.db) if args.db else None)


def _profile(args) -> Profile:
    return Profile.load(Path(args.profile) if args.profile else None)


def _length(text: Optional[str], unit: str) -> Optional[float]:
    if text is None:
        return None
    try:
        return parse_length(text, default_unit=unit)
    except ValueError as error:
        raise SystemExit(str(error))


def _extractor():
    client = None

    def run(text: str, url: str):
        nonlocal client
        if client is None:
            from .extract import make_client

            client = make_client()
        return extract(text, url, client=client)

    return run


def _recommend_all(store: Store, profile: Profile, brand: Optional[str] = None) -> List[Recommendation]:
    bias = store.brand_bias()
    charts = {}
    results = []
    for product in store.products(brand):
        if product.brand not in charts:
            charts[product.brand] = store.brand_chart(product.brand)
        results.append(recommend(product, profile, charts[product.brand], bias.get(product.brand, 0.0)))
    return results


def _short(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_recommendation_table(recs: Sequence[Recommendation]) -> None:
    for rec in recs:
        title = _short(f"{rec.product.title} ({rec.product.brand})", 44)
        print(f"  {title:<44}  {rec.label:<5} {rec.score:4.2f}  {rec.confidence:<8}  {rec.reason}")


def _print_sizes(rec: Recommendation) -> None:
    for size in rec.sizes:
        diffs = ", ".join(
            f"{name} {d:+.1f}" for name, d in size.diffs.items() if not name.startswith("_")
        )
        marker = "<-" if size.size in (rec.size, rec.runner_up) else "  "
        print(f"    {size.size:<6} {size.score:4.2f}  {marker} {diffs}")


def _require_profile(profile: Profile) -> None:
    if profile.empty:
        raise SystemExit(
            "No measurements yet. Start with, for example:\n"
            "  fitfind profile set --shirt-pit 21 --shirt-length 29.5\n"
            "  fitfind profile set --chest 40 --waist 34 --height 5'11\""
        )


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def cmd_profile_set(args) -> int:
    profile = _profile(args)
    unit = args.unit
    body = {
        "chest": _length(args.chest, unit),
        "waist": _length(args.waist, unit),
        "neck": _length(args.neck, unit),
        "sleeve": _length(args.sleeve, unit),
        "height": _length(args.height, unit),
    }
    shirt = {
        "chest": _length(args.shirt_chest, unit),
        "length": _length(args.shirt_length, unit),
        "shoulder": _length(args.shirt_shoulder, unit),
        "sleeve": _length(args.shirt_sleeve, unit),
    }
    if args.shirt_pit is not None:
        shirt["chest"] = _length(args.shirt_pit, unit) * 2
    profile.body.update({k: round(v, 2) for k, v in body.items() if v is not None})
    profile.shirt.update({k: round(v, 2) for k, v in shirt.items() if v is not None})
    for name in args.clear or []:
        if name.startswith("shirt-"):
            profile.shirt.pop(name.removeprefix("shirt-"), None)
        else:
            profile.body.pop(name, None)
    if args.fit:
        profile.fit = args.fit
    if args.priority:
        profile.priority = args.priority
    if args.tight_penalty is not None:
        profile.tight_penalty = args.tight_penalty
    path = profile.save(Path(args.profile) if args.profile else None)
    print(f"Saved to {path}")
    return cmd_profile_show(args)


def cmd_profile_show(args) -> int:
    profile = _profile(args)
    if args.json:
        print(json.dumps(profile.to_dict(), indent=2))
        return 0
    if profile.empty:
        print("No measurements yet. See: fitfind profile set --help")
        return 0
    print("Body (tape measure)")
    for name, value in profile.body.items():
        shown = format_height(value) if name == "height" else format_inches(value)
        print(f"  {name:<9} {shown}")
    print("Reference shirt (laid flat)")
    for name, value in profile.shirt.items():
        print(f"  {name:<9} {format_inches(value)}")
    print(f"Fit          {profile.fit}")
    print(f"Priority     {profile.priority}")
    print(f"Tight costs  {profile.tight_penalty:g}x as much as loose")
    return 0


def cmd_collect(args) -> int:
    if args.brand:
        brands = [domain_of(b) for b in args.brand]
    else:
        path = Path(args.brands)
        if not path.exists():
            raise SystemExit(f"{path} not found. Copy brands.example.txt to brands.txt and add stores.")
        brands = read_brands(path)
    if not brands:
        raise SystemExit("No brands to collect.")
    store = _store(args)
    fetcher = Fetcher(store, delay=args.delay, max_age_days=args.max_age)
    progress = (lambda message: print("  " + message, file=sys.stderr)) if args.verbose else (lambda m: None)
    extractor = _extractor()
    for brand in brands:
        print(f"{brand}")
        report = collect_brand(
            brand, store, fetcher, extractor, limit=args.limit, reextract=args.reextract, progress=progress
        )
        print(f"  source        {report.source}")
        print(f"  shirts        {report.shirts} of {report.seen} products")
        print(f"  read now      {report.extracted}")
        print(f"  with sizing   {report.with_sizing}")
        print(f"  brand chart   {'yes' if report.brand_chart or store.brand_chart(brand) else 'no'}")
        for error in report.errors[:5]:
            print(f"  ! {error}")
        if len(report.errors) > 5:
            print(f"  ! ...and {len(report.errors) - 5} more")
    return 0


def cmd_rank(args) -> int:
    profile = _profile(args)
    if args.fit:
        profile.fit = args.fit
    _require_profile(profile)
    store = _store(args)
    recs = rank(_recommend_all(store, profile, domain_of(args.brand) if args.brand else None))
    floor = CONFIDENCE_ORDER[args.min_confidence]
    recs = [r for r in recs if CONFIDENCE_ORDER.get(r.confidence, 9) <= floor]
    if args.query:
        words = [w.lower() for w in args.query]
        recs = [r for r in recs if all(w in r.product.title.lower() for w in words)]
    recs = recs[: args.top]
    if args.json:
        print(json.dumps([
            {
                "title": r.product.title, "brand": r.product.brand, "url": r.product.url,
                "size": r.size, "between": r.runner_up, "score": round(r.score, 3),
                "confidence": r.confidence, "method": r.method, "reason": r.reason,
            }
            for r in recs
        ], indent=2))
        return 0
    if not recs:
        print("Nothing to rank yet. Run `fitfind collect` first, or lower --min-confidence.")
        return 0
    print(f"Best fits for a {profile.fit} fit\n")
    _print_recommendation_table(recs)
    return 0


def cmd_check(args) -> int:
    profile = _profile(args)
    if args.fit:
        profile.fit = args.fit
    _require_profile(profile)
    store = _store(args)
    fetcher = Fetcher(store)
    try:
        product = check_url(args.url, store, fetcher, _extractor())
    except ExtractionError as error:
        raise SystemExit(str(error))
    if product is None:
        raise SystemExit(f"Could not fetch {args.url} (blocked by robots.txt, or the request failed).")
    rec = recommend(
        product, profile, store.brand_chart(product.brand), store.brand_bias().get(product.brand, 0.0)
    )
    print(product.title)
    print(f"  {product.url}")
    if rec.size is None:
        print(f"  No recommendation: {rec.reason}")
        return 1
    print(f"\n  Buy {rec.label}   score {rec.score:.2f}, confidence {rec.confidence}")
    print(f"  {rec.reason}\n")
    _print_sizes(rec)
    return 0


def cmd_show(args) -> int:
    store = _store(args)
    product = store.product(args.url)
    if product is None:
        raise SystemExit("Not collected yet. Run `fitfind check URL` first.")
    print(json.dumps(product.to_dict(), indent=2))
    chart = store.brand_chart(product.brand)
    if chart:
        print("\nBrand-wide chart:")
        print(json.dumps(chart.to_dict(), indent=2))
    return 0


def cmd_owned(args) -> int:
    store = _store(args)
    if not args.url:
        items = store.owned()
        if not items:
            print("No shirts recorded yet.")
        for item in items:
            print(f"  {item.size:<5} {item.fit:<6} {_short(item.title, 50):<50} {item.brand}")
        bias = store.brand_bias()
        if bias:
            print("\nBrand adjustments")
            for brand, inches in sorted(bias.items()):
                way = "runs small" if inches > 0 else "runs large"
                print(f"  {brand:<30} {way}, {inches:+.1f} in")
        return 0
    if not args.size or not args.fit:
        raise SystemExit("Give --size and --fit (good, tight or loose).")
    product = store.product(args.url)
    if product is None:
        fetcher = Fetcher(store)
        try:
            product = check_url(args.url, store, fetcher, _extractor())
        except ExtractionError as error:
            raise SystemExit(str(error))
        if product is None:
            raise SystemExit(f"Could not fetch {args.url}.")
    owned = store.save_owned(product, normalise_size(args.size), args.fit)
    print(f"Recorded {owned.title} in {owned.size}: {owned.fit}")
    return 0


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fitfind", description="Which size to buy, product by product, from your own measurements."
    )
    parser.add_argument("--db", help="SQLite file (default ~/.fitfind/fitfind.db)")
    parser.add_argument("--profile", help="profile JSON (default ~/.fitfind/profile.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile", help="set or show your measurements")
    psub = profile.add_subparsers(dest="action", required=True)
    pset = psub.add_parser("set", help="set measurements; numbers default to --unit")
    pset.add_argument("--unit", choices=("in", "cm"), default="in")
    body = pset.add_argument_group("body, with a tape measure")
    body.add_argument("--chest", help="fullest part, under the arms")
    body.add_argument("--waist")
    body.add_argument("--neck")
    body.add_argument("--sleeve", help="centre back of neck to wrist")
    body.add_argument("--height", help="e.g. 5'11\" or 180cm")
    shirt = pset.add_argument_group("a shirt that fits you well, laid flat")
    shirt.add_argument("--shirt-pit", help="armpit to armpit across the front (doubled for chest)")
    shirt.add_argument("--shirt-chest", help="full chest circumference, if you have it")
    shirt.add_argument("--shirt-length", help="top of the collar seam to the hem, at the back")
    shirt.add_argument("--shirt-shoulder", help="shoulder seam to shoulder seam")
    shirt.add_argument("--shirt-sleeve", help="shoulder seam to cuff")
    pset.add_argument("--fit", choices=FITS)
    pset.add_argument("--priority", choices=("chest", "length", "shoulder", "sleeve", "waist"))
    pset.add_argument("--tight-penalty", type=float, help="how much more too-tight costs than too-loose")
    pset.add_argument("--clear", nargs="*", help="remove measurements, e.g. --clear waist shirt-sleeve")
    pset.add_argument("--json", action="store_true")
    pset.set_defaults(func=cmd_profile_set)
    pshow = psub.add_parser("show")
    pshow.add_argument("--json", action="store_true")
    pshow.set_defaults(func=cmd_profile_show)

    collect = sub.add_parser("collect", help="read shirts and sizing from your brands")
    collect.add_argument("--brands", default="brands.txt", help="file of store domains")
    collect.add_argument("--brand", action="append", help="collect just this store (repeatable)")
    collect.add_argument("--limit", type=int, default=200, help="most shirts per brand")
    collect.add_argument("--delay", type=float, default=1.0, help="seconds between requests to a store")
    collect.add_argument("--max-age", type=float, default=14, help="refetch pages older than this many days")
    collect.add_argument("--reextract", action="store_true", help="read sizing again even if already read")
    collect.add_argument("-v", "--verbose", action="store_true")
    collect.set_defaults(func=cmd_collect)

    rank_p = sub.add_parser("rank", help="rank collected shirts for you")
    rank_p.add_argument("query", nargs="*", help="only titles containing these words")
    rank_p.add_argument("--fit", choices=FITS, help="override your saved fit for this run")
    rank_p.add_argument("--brand")
    rank_p.add_argument("--top", type=int, default=20)
    rank_p.add_argument("--min-confidence", choices=CONFIDENCES, default="low")
    rank_p.add_argument("--json", action="store_true")
    rank_p.set_defaults(func=cmd_rank)

    check = sub.add_parser("check", help="size one product from its URL")
    check.add_argument("url")
    check.add_argument("--fit", choices=FITS)
    check.set_defaults(func=cmd_check)

    show = sub.add_parser("show", help="print what was collected for one product")
    show.add_argument("url")
    show.set_defaults(func=cmd_show)

    owned = sub.add_parser("owned", help="record how a shirt you bought fits; no URL lists them")
    owned.add_argument("url", nargs="?")
    owned.add_argument("--size")
    owned.add_argument("--fit", choices=OWNED_FITS)
    owned.set_defaults(func=cmd_owned)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not hasattr(args, "json"):
        args.json = False
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
