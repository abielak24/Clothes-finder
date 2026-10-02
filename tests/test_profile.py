import pytest

from fitfind.profile import Profile


def test_reference_shirt_is_used_as_it_stands():
    profile = Profile(body={"chest": 39}, shirt={"chest": 42}, fit="slim")
    assert profile.garment_targets()["chest"] == 42


def test_body_plus_ease_fills_in_for_a_missing_shirt():
    assert Profile(body={"chest": 39}, fit="regular").garment_targets()["chest"] == 43.5
    assert Profile(body={"chest": 39}, fit="slim").garment_targets()["chest"] == 41.5


def test_body_sleeve_is_never_compared_with_garment_sleeve():
    # Measured from different starting points, so not comparable.
    assert "sleeve" not in Profile(body={"sleeve": 34}).garment_targets()


def test_round_trip(tmp_path):
    path = tmp_path / "p.json"
    Profile(body={"chest": 40}, shirt={"length": 29}, fit="relaxed", tight_penalty=2).save(path)
    loaded = Profile.load(path)
    assert loaded.body == {"chest": 40} and loaded.shirt == {"length": 29}
    assert loaded.fit == "relaxed" and loaded.tight_penalty == 2


def test_missing_file_is_an_empty_profile(tmp_path):
    assert Profile.load(tmp_path / "none.json").empty


def test_unknown_values_are_rejected():
    with pytest.raises(ValueError):
        Profile(fit="baggy")
    with pytest.raises(ValueError):
        Profile(body={"inseam": 32})
