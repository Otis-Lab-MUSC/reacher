"""Version parsing and ordering for the update checker."""

import pytest

from reacher.update.versions import Stage, VersionError, parse_version


def test_parse_stable_tag_with_prefix():
    version = parse_version("v3.0.0")
    assert (version.major, version.minor, version.patch) == (3, 0, 0)
    assert version.stage is Stage.STABLE
    assert version.number == 0
    assert not version.is_prerelease


def test_parse_prerelease_tag():
    version = parse_version("v3.0.1-beta.16")
    assert version.stage is Stage.BETA
    assert version.number == 16
    assert version.is_prerelease


def test_parse_without_prefix():
    assert parse_version("3.4.0-rc.2") == parse_version("v3.4.0-rc.2")


@pytest.mark.parametrize(
    "text", ["", "v", "3.0", "3.0.0-gamma.1", "3.0.0-beta", "3.0.0-beta.x", "latest", "3.0.0-beta.1.2"]
)
def test_parse_rejects_malformed(text):
    with pytest.raises(VersionError):
        parse_version(text)


def test_beta_numbers_compare_numerically_not_lexically():
    assert parse_version("3.0.1-beta.16") > parse_version("3.0.1-beta.9")
    assert parse_version("3.0.1-beta.16") > parse_version("3.0.1-beta.15")


def test_adjacent_betas_are_not_equal():
    assert parse_version("3.0.1-beta.16") != parse_version("3.0.1-beta.15")


def test_stage_ladder_orders_alpha_beta_rc_stable():
    ordered = ["3.0.1-alpha.9", "3.0.1-beta.1", "3.0.1-rc.1", "3.0.1"]
    versions = [parse_version(text) for text in ordered]
    assert sorted(reversed(versions)) == versions


def test_prerelease_of_next_patch_beats_older_stable():
    assert parse_version("3.0.1-beta.16") > parse_version("3.0.0")


def test_stable_beats_its_own_prereleases():
    assert parse_version("3.0.1") > parse_version("3.0.1-rc.9")


def test_core_numbers_compare_numerically():
    assert parse_version("3.10.0") > parse_version("3.9.0")


def test_str_round_trips_to_tag_form():
    assert str(parse_version("v3.0.1-beta.16")) == "3.0.1-beta.16"
    assert str(parse_version("3.0.0")) == "3.0.0"
