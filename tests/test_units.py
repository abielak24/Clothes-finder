import pytest

from fitfind.units import format_height, parse_length


@pytest.mark.parametrize(
    "text, unit, expected",
    [
        ("40", "in", 40.0),
        ("40in", "cm", 40.0),
        ("101.6 cm", "in", 40.0),
        ("101.6", "cm", 40.0),
        ("6'1\"", "in", 73.0),
        ("6'", "in", 72.0),
        ("5 ft 11", "in", 71.0),
    ],
)
def test_parse_length(text, unit, expected):
    assert parse_length(text, default_unit=unit) == pytest.approx(expected)


def test_parse_length_rejects_nonsense():
    with pytest.raises(ValueError):
        parse_length("medium")


def test_format_height():
    assert format_height(73) == "6'1\""
