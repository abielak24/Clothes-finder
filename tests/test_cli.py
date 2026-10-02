"""Command-line behaviour, end to end against a temporary store."""

from __future__ import annotations

import json

import pytest
from conftest import body_chart, garment_chart

from fitfind.cli import main
from fitfind.products import Product
from fitfind.store import Store


@pytest.fixture
def paths(tmp_path):
    return ["--db", str(tmp_path / "f.db"), "--profile", str(tmp_path / "p.json")]


@pytest.fixture
def stocked(paths, tmp_path, oxford, tee):
    store = Store(tmp_path / "f.db")
    for product in (oxford, tee):
        product.extracted = True
        store.save_product(product)
    store.close()
    return paths


def test_profile_set_accepts_pit_to_pit_and_heights(paths, capsys):
    assert main(paths + ["profile", "set", "--shirt-pit", "21", "--height", "6'0\"", "--fit", "slim"]) == 0
    capsys.readouterr()
    assert main(paths + ["profile", "show", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["shirt"]["chest"] == 42
    assert data["body"]["height"] == 72
    assert data["fit"] == "slim"


def test_profile_set_in_centimetres(paths, capsys):
    main(paths + ["profile", "set", "--unit", "cm", "--chest", "101.6"])
    capsys.readouterr()
    main(paths + ["profile", "show", "--json"])
    assert json.loads(capsys.readouterr().out)["body"]["chest"] == 40


def test_profile_clear(paths, capsys):
    main(paths + ["profile", "set", "--chest", "40", "--shirt-length", "29"])
    main(paths + ["profile", "set", "--clear", "chest", "shirt-length"])
    capsys.readouterr()
    main(paths + ["profile", "show", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["body"] == {} and data["shirt"] == {}


def test_rank_needs_a_profile(stocked):
    with pytest.raises(SystemExit) as error:
        main(stocked + ["rank"])
    assert "No measurements yet" in str(error.value)


def test_rank_prints_size_and_reason(stocked, capsys):
    main(stocked + ["profile", "set", "--shirt-chest", "42", "--shirt-length", "29.5"])
    capsys.readouterr()
    assert main(stocked + ["rank"]) == 0
    out = capsys.readouterr().out
    first = out.splitlines()[2]
    assert "Oxford shirt" in first and " L " in first and "high" in first


def test_rank_json_and_filters(stocked, capsys):
    main(stocked + ["profile", "set", "--chest", "39"])
    capsys.readouterr()
    main(stocked + ["rank", "--json", "tee"])
    data = json.loads(capsys.readouterr().out)
    assert [d["title"] for d in data] == ["Everyday tee"]
    assert data[0]["size"] == "M"
    main(stocked + ["rank", "--json", "--min-confidence", "high"])
    assert json.loads(capsys.readouterr().out) == []


def test_owned_feedback_shifts_the_brand(stocked, capsys):
    main(stocked + ["profile", "set", "--chest", "40.5"])
    capsys.readouterr()
    main(stocked + ["rank", "--json", "tee"])
    assert json.loads(capsys.readouterr().out)[0]["size"] == "M"

    url = "https://north.test/products/tee"
    assert main(stocked + ["owned", url, "--size", "medium", "--fit", "tight"]) == 0
    main(stocked + ["owned"])
    out = capsys.readouterr().out
    assert "M     tight" in out and "runs small" in out

    main(stocked + ["rank", "--json", "tee"])
    assert json.loads(capsys.readouterr().out)[0]["size"] == "L"
