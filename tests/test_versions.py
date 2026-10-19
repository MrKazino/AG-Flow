"""Version handling for the updater and the Antigravity compatibility checks (stdlib only, total on bad input)."""

from __future__ import annotations

import pytest

from agflow.versions import bump_kind, newest, numeric_tuple, parse


@pytest.mark.parametrize("text, expected", [
    ("0.4.0", (0, 4, 0)), ("v0.4.0", (0, 4, 0)), ("V1.2.3", (1, 2, 3)), (" 1.0 ", (1, 0, 0)), ("2", (2, 0, 0)),
    ("0.10.0", (0, 10, 0)),
])
def test_release_versions_parse(text, expected):
    v = parse(text)
    assert v is not None and (v.major, v.minor, v.patch) == expected and not v.is_prerelease


@pytest.mark.parametrize("text", ["", "   ", "latest", "x.y.z", "1.2.3.4.5", "1..2", "-1.0.0", None, 3, "v", "0.4.0+"])
def test_garbage_is_none_never_an_exception(text):
    assert parse(text) is None


def test_order_is_numeric_not_alphabetical():
    assert parse("0.10.0") > parse("0.9.0") > parse("0.2.0")
    assert parse("1.0.0") > parse("0.99.99")
    assert sorted(map(parse, ["0.10.0", "0.2.0", "0.9.1"]))[-1] == parse("0.10.0")


def test_prereleases_sort_before_their_release_in_the_conventional_order():
    order = ["0.4.0.dev1", "0.4.0a1", "0.4.0b2", "0.4.0rc1", "0.4.0-rc.2", "0.4.0"]
    versions = [parse(t) for t in order]
    assert all(v is not None for v in versions)
    assert versions == sorted(versions)
    assert parse("0.4.0rc1") < parse("0.4.0") and parse("0.4.0") < parse("0.4.1.dev0")
    assert parse("0.4.0rc1").is_prerelease and not parse("0.4.0").is_prerelease


def test_equality_ignores_the_v_prefix_and_trailing_zero_components():
    assert parse("v1.2") == parse("1.2.0") and hash(parse("v1.2")) == hash(parse("1.2.0"))


def test_str_round_trips_to_a_canonical_form():
    assert str(parse("v0.4.0")) == "0.4.0" and str(parse("0.4.0-rc.2")) == "0.4.0rc2"


@pytest.mark.parametrize("old, new, kind", [
    ("0.3.0", "0.3.1", "patch"), ("0.3.9", "0.4.0", "minor"), ("0.9.0", "1.0.0", "major"),
    ("0.3.0", "0.4.0rc1", "pre"), ("0.4.0rc1", "0.4.0", "patch"), ("0.4.0", "0.4.0", "same"),
    ("0.4.0", "0.3.0", "downgrade"),
])
def test_bump_kind(old, new, kind):
    assert bump_kind(parse(old), parse(new)) == kind


def test_newest_filters_by_channel():
    versions = [parse(t) for t in ("0.3.0", "0.4.0rc1", "0.3.5", "0.4.0b1")]
    assert newest(versions, include_prereleases=False) == parse("0.3.5")
    assert newest(versions, include_prereleases=True) == parse("0.4.0rc1")
    assert newest([], include_prereleases=True) is None
    assert newest([None, parse("1.0.0")], include_prereleases=False) == parse("1.0.0")  # unparseable tags are skipped


@pytest.mark.parametrize("text, expected", [
    ("1.3.3", (1, 3, 3)), ("agy 1.3.3", (1, 3, 3)), ("Antigravity CLI v1.10.0 (build 7)", (1, 10, 0)),
    ("0.1.20", (0, 1, 20)), ("nothing here", ()), ("", ()), (None, ()),
])
def test_numeric_tuple_reads_the_first_dotted_number_in_a_tool_banner(text, expected):
    assert numeric_tuple(text) == expected
    assert numeric_tuple("1.10.0") > numeric_tuple("1.9.9"), "tuples compare numerically"


@pytest.mark.parametrize("spec, have, expected", [
    (">=3.10", (3, 12, 1), True), (">=3.10", (3, 9, 18), False), (">=3.10,<4", (4, 0), False),
    (">=3.10, <4", (3, 10), True), (">3.10", (3, 10), False), ("==3.11", (3, 11, 4), False),
    ("==3.11.0", (3, 11), True), ("!=3.12", (3, 12, 0), False), ("<=3.12", (3, 12, 9), False),
    ("", (3, 10), True), ("   ", (3, 10), True), ("~=3.10", (3, 10), None), (">=three", (3, 10), None),
    (">=3.10,junk", (3, 10), None), (None, (3, 10), True),
])
def test_satisfies(spec, have, expected):
    from agflow.versions import satisfies

    assert satisfies(spec, have) is expected
