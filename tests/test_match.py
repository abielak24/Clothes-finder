"""Size recommendations from each kind of evidence."""

from __future__ import annotations

import pytest
from conftest import body_chart, garment_chart

from fitfind.match import chart_from_model_notes, rank, recommend
from fitfind.products import ModelNote, Product
from fitfind.profile import Profile


# -- garment charts -----------------------------------------------------
def test_garment_chart_against_reference_shirt(oxford, shirt_profile):
    rec = recommend(oxford, shirt_profile)
    assert rec.size == "L"
    assert rec.method == "garment chart"
    assert rec.confidence == "high"
    assert "reference shirt" in rec.reason
    assert "chest +0.5 in" in rec.reason


def test_too_tight_costs_more_than_too_loose():
    product = Product("u", "b", "t", charts=[garment_chart({"M": {"chest": 40.5}, "L": {"chest": 42.5}})])
    profile = Profile(shirt={"chest": 41.5})
    rec = recommend(product, profile)
    assert rec.size == "L"
    scores = {s.size: s.score for s in rec.sizes}
    assert scores["L"] > scores["M"]


def test_stretch_fabric_removes_the_asymmetry():
    product = Product("u", "b", "t", stretch=True,
                      charts=[garment_chart({"M": {"chest": 40.5}, "L": {"chest": 42.5}})])
    rec = recommend(product, Profile(shirt={"chest": 41.5}))
    scores = {s.size: s.score for s in rec.sizes}
    assert scores["L"] == pytest.approx(scores["M"])


def test_body_measurements_stand_in_for_a_missing_shirt(oxford):
    # 38 in chest + 4.5 in regular ease = 42.5 in garment: an exact L.
    rec = recommend(oxford, Profile(body={"chest": 38}))
    assert rec.size == "L"
    assert rec.confidence == "medium"
    assert "regular ease" in rec.reason


def test_priority_measurement_wins_a_disagreement():
    # M fits the chest, L fits the length.
    product = Product("u", "b", "t", charts=[garment_chart({
        "M": {"chest": 42, "length": 28},
        "L": {"chest": 44, "length": 30},
    })])
    by_chest = Profile(shirt={"chest": 42, "length": 30}, priority="chest")
    by_length = Profile(shirt={"chest": 42, "length": 30}, priority="length")
    assert recommend(product, by_chest).size == "M"
    assert recommend(product, by_length).size == "L"


# -- body charts --------------------------------------------------------
def test_body_chart_range(tee, body_profile):
    rec = recommend(tee, body_profile)
    assert rec.size == "M"
    assert rec.method == "body chart"
    assert rec.confidence == "medium"
    assert "chest spot on" in rec.reason


def test_between_ranges_prefers_room_to_spare(tee):
    # 41.5 in sits half an inch above M and half an inch below L.
    rec = recommend(tee, Profile(body={"chest": 41.5}))
    assert rec.size == "L"


@pytest.mark.parametrize("fit, expected", [("slim", "M"), ("relaxed", "L")])
def test_exact_ties_follow_the_preferred_fit(fit, expected):
    product = Product("u", "b", "t", charts=[body_chart({"M": {"chest": (38, 41)}, "L": {"chest": (41, 44)}})])
    rec = recommend(product, Profile(body={"chest": 41}, fit=fit))
    assert rec.size == expected
    assert rec.label == "M/L"


def test_body_chart_from_reference_shirt_is_low_confidence(tee):
    # 43.5 in shirt - 4.5 in regular ease = 39 in body.
    rec = recommend(tee, Profile(shirt={"chest": 43.5}))
    assert rec.size == "M"
    assert rec.confidence == "low"
    assert "estimated" in rec.reason


def test_brand_bias_tips_a_close_call(tee):
    profile = Profile(body={"chest": 40.5})
    assert recommend(tee, profile).size == "M"
    rec = recommend(tee, profile, bias=1.0)
    assert rec.size == "L"
    assert "how this brand fit you" in rec.reason


# -- model notes --------------------------------------------------------
def test_model_notes_become_a_chart(linen):
    chart = chart_from_model_notes(linen)
    assert chart.sizes["M"]["chest"] == (39, 41)
    assert chart.sizes["L"]["chest"] == (41, 43)
    assert chart.sizes["S"]["chest"] == (37, 39)


def test_model_notes_recommendation(linen, body_profile):
    rec = recommend(linen, body_profile)
    assert rec.size == "M"
    assert rec.method == "model notes"
    assert rec.confidence == "low"
    assert "Model is 6'1\"" in rec.reason


def test_model_notes_between_sizes(linen):
    rec = recommend(linen, Profile(body={"chest": 41}))
    assert rec.label == "M/L"


def test_height_only_model_note():
    product = Product("u", "b", "t", sizes=["S", "M", "L"],
                      model_notes=[ModelNote(size="M", height=73, evidence="Model is 6'1\" wearing M")])
    assert recommend(product, Profile(body={"height": 76})).size == "L"


def test_model_notes_need_ordered_sizes():
    product = Product("u", "b", "t", sizes=["Tall", "Regular"],
                      model_notes=[ModelNote(size="Regular", chest=40)])
    assert chart_from_model_notes(product) is None


# -- fallbacks and ranking ----------------------------------------------
def test_product_chart_beats_model_notes(oxford, shirt_profile):
    oxford.model_notes.append(ModelNote(size="S", chest=44))
    assert recommend(oxford, shirt_profile).method == "garment chart"


def test_brand_chart_is_the_last_resort(shirt_profile, oxford):
    bare = Product("u", "acme.test", "Plain shirt", sizes=["M", "L"])
    rec = recommend(bare, shirt_profile, brand_chart=oxford.charts[0])
    assert rec.size == "L"
    assert rec.confidence == "very low"
    assert rec.method.startswith("brand-wide")


def test_no_evidence_no_recommendation(shirt_profile):
    rec = recommend(Product("u", "b", "Mystery shirt"), shirt_profile)
    assert rec.size is None
    assert "no size chart" in rec.reason


def test_rank_orders_by_score_and_drops_unknowns(oxford, tee, shirt_profile):
    bare = Product("u", "b", "Mystery shirt")
    recs = rank([recommend(p, shirt_profile) for p in (tee, oxford, bare)])
    assert [r.product.title for r in recs][0] == "Oxford shirt"
    assert all(r.size for r in recs)


def test_half_an_inch_either_way_is_a_perfect_fit():
    product = Product("u", "b", "t", charts=[garment_chart({"L": {"chest": 42.5, "length": 29}})])
    rec = recommend(product, Profile(shirt={"chest": 42, "length": 29.5}))
    assert rec.score == pytest.approx(1.0)
