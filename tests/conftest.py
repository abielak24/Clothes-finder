"""Shared fixtures: products with each kind of sizing evidence."""

from __future__ import annotations

import pytest

from fitfind.products import BODY, GARMENT, ModelNote, Product, SizeChart
from fitfind.profile import Profile


def garment_chart(rows):
    """rows: {size: {measurement: value}} with single garment values."""
    return SizeChart(
        kind=GARMENT,
        sizes={s: {n: (v, v) for n, v in m.items()} for s, m in rows.items()},
        evidence="Garment measurements",
    )


def body_chart(rows):
    """rows: {size: {measurement: (low, high)}}."""
    return SizeChart(kind=BODY, sizes=rows, evidence="Size guide")


@pytest.fixture
def shirt_profile():
    # A 42 in reference shirt: the medium-or-large person.
    return Profile(shirt={"chest": 42.0, "length": 29.5}, fit="regular")


@pytest.fixture
def body_profile():
    return Profile(body={"chest": 39.0, "waist": 33.0, "height": 72.0}, fit="regular")


@pytest.fixture
def oxford():
    return Product(
        url="https://acme.test/products/oxford",
        brand="acme.test",
        title="Oxford shirt",
        sizes=["S", "M", "L", "XL"],
        charts=[garment_chart({
            "S": {"chest": 38, "length": 28},
            "M": {"chest": 40.5, "length": 29},
            "L": {"chest": 42.5, "length": 30},
            "XL": {"chest": 45, "length": 31},
        })],
    )


@pytest.fixture
def tee():
    return Product(
        url="https://north.test/products/tee",
        brand="north.test",
        title="Everyday tee",
        sizes=["S", "M", "L"],
        charts=[body_chart({
            "S": {"chest": (34, 37)},
            "M": {"chest": (38, 41)},
            "L": {"chest": (42, 45)},
        })],
    )


@pytest.fixture
def linen():
    return Product(
        url="https://harbor.test/products/linen",
        brand="harbor.test",
        title="Linen shirt",
        sizes=["S", "M", "L", "XL"],
        model_notes=[ModelNote(size="M", height=73, chest=40, evidence="Model is 6'1\", 40 in chest, wearing M")],
    )
