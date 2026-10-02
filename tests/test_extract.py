"""Turning page text and Claude's answers into charts in inches."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fitfind.extract import (
    Chart, ChartCell, Extraction, ExtractionError, ModelFact, apply_extraction,
    classify_unclear, extract, html_to_text, mentions_sizing, to_size_charts,
)
from fitfind.products import BODY, GARMENT, Product, SizeChart


def extraction(**overrides) -> Extraction:
    data = dict(charts=[], model_notes=[], fit_name="not stated", stretch="not stated",
                sizes_offered=[], size_chart_url="")
    data.update(overrides)
    return Extraction(**data)


def cells(rows, measurement="chest"):
    return [ChartCell(size=s, measurement=measurement, low=lo, high=hi) for s, (lo, hi) in rows.items()]


# -- page text ----------------------------------------------------------
def test_html_to_text_keeps_table_rows_together():
    text = html_to_text(
        "<p>Model is 6'1\" wearing M</p><script>var x=1</script>"
        "<table><tr><th>Size</th><th>Chest</th></tr><tr><td>M</td><td>40</td></tr></table>"
    )
    assert "Model is 6'1\" wearing M" in text
    assert "var x" not in text
    assert "| M | 40" in text


def test_html_to_text_reads_json_ld_and_size_links():
    markup = (
        '<script type="application/ld+json">{"@type":"Product","description":"Chest 40 &amp; up"}</script>'
        '<a href="/pages/size-guide">Size guide</a>'
    )
    text = html_to_text(markup)
    assert "Chest 40 & up" in text
    assert "/pages/size-guide" in text


def test_pages_without_sizing_words_are_skipped():
    assert not mentions_sizing("Soft cotton. Machine wash cold.")
    assert mentions_sizing("Model wears a medium")


# -- conversion ---------------------------------------------------------
def test_centimetres_are_converted():
    chart = Chart(kind="body", unit="cm", cells=cells({"M": (96.52, 101.6)}), quote="Size guide")
    [result] = to_size_charts(extraction(charts=[chart]), "u")
    lo, hi = result.sizes["M"]["chest"]
    assert (lo, hi) == (pytest.approx(38), pytest.approx(40))


def test_size_labels_are_normalised():
    chart = Chart(kind="garment", unit="in", cells=cells({"Medium": (42, 42), "2XL": (48, 48)}), quote="")
    [result] = to_size_charts(extraction(charts=[chart]), "u")
    assert set(result.sizes) == {"M", "XXL"}


def test_a_missed_half_chest_is_doubled():
    chart = Chart(kind="garment", unit="in", cells=cells({"M": (21, 21)}), quote="Pit to pit")
    [result] = to_size_charts(extraction(charts=[chart]), "u")
    assert result.sizes["M"]["chest"] == (42, 42)


@pytest.mark.parametrize(
    "rows, expected",
    [
        ({"M": {"chest": (39, 39)}}, BODY),
        ({"M": {"chest": (43, 43)}}, GARMENT),
        ({"M": {"chest": (39, 39), "length": (29, 29)}}, GARMENT),
    ],
)
def test_unclear_charts_are_classified_by_their_numbers(rows, expected):
    assert classify_unclear(SizeChart(kind=BODY, sizes=rows)) == expected


def test_unclear_chart_records_the_inference():
    chart = Chart(kind="unclear", unit="in", cells=cells({"M": (43, 43)}), quote="Sizes")
    [result] = to_size_charts(extraction(charts=[chart]), "u")
    assert result.kind == GARMENT
    assert "type inferred" in result.evidence


def test_apply_fills_the_product():
    product = Product("u", "b", "t")
    result = extraction(
        charts=[Chart(kind="body", unit="in", cells=cells({"M": (38, 40)}), quote="Fit guide")],
        model_notes=[ModelFact(size_worn="medium", height_in=73, chest_in=0, waist_in=0, quote="Model is 6'1\"")],
        fit_name="slim", stretch="yes", sizes_offered=["Small", "Medium", "Large"],
    )
    apply_extraction(product, result, "u")
    assert product.extracted
    assert product.chart(BODY).sizes["M"]["chest"] == (38, 40)
    note = product.model_notes[0]
    assert (note.size, note.height, note.chest) == ("M", 73, None)
    assert product.fit_name == "slim" and product.stretch is True
    assert product.sizes == ["S", "M", "L"]


# -- the Claude call ----------------------------------------------------
class FakeClient:
    def __init__(self, response):
        self.calls = []
        self.response = response
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_extract_sends_a_schema_and_returns_the_answer():
    answer = extraction(fit_name="regular")
    client = FakeClient(SimpleNamespace(stop_reason="end_turn", parsed_output=answer))
    assert extract("Chest 40", "https://x.test/p", client=client) is answer
    call = client.calls[0]
    assert call["output_format"] is Extraction
    assert call["fallbacks"] == "default"
    assert "Chest 40" in call["messages"][0]["content"]


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_extract_reports_unusable_answers(stop):
    client = FakeClient(SimpleNamespace(stop_reason=stop, parsed_output=None))
    with pytest.raises(ExtractionError):
        extract("Chest 40", "https://x.test/p", client=client)
