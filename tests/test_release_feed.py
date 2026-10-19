"""The release feed: parsing, selection, and a network layer that is pinned, bounded and credential-safe.

Everything runs against a fake GitHub on loopback (tests/fake_github.py); nothing touches the network."""

from __future__ import annotations

import hashlib
import urllib.request

import pytest

from agflow import release_feed as rf
from agflow import versions
from tests.fake_github import REPO, FakeGitHub


@pytest.fixture
def gh():
    fake = FakeGitHub().start()
    yield fake
    fake.stop()


def feed(gh, **kw) -> rf.Feed:
    return rf.Feed(REPO, api=gh.base, allow_loopback=True, token=kw.pop("token", None), **kw)


# --- parsing --------------------------------------------------------------------------------------


def test_drafts_unreadable_entries_and_unparseable_tags_are_skipped(gh):
    gh.add_release("0.4.0")
    gh.add_release("0.5.0", draft=True)
    gh.releases.append({"tag_name": "nightly", "assets": []})
    gh.releases.append("junk")
    got = feed(gh).list_releases().releases
    assert [r.tag for r in got] == ["v0.4.0"]


def test_a_release_is_usable_only_with_its_wheel_and_its_checksum_list(gh):
    gh.add_release("0.4.0")
    gh.add_release("0.4.1", sums=False)
    gh.add_release("0.4.2", wheel=None)
    by_tag = {r.tag: r for r in feed(gh).list_releases().releases}
    assert by_tag["v0.4.0"].usable and by_tag["v0.4.0"].wheel.name == "agflow-0.4.0-py3-none-any.whl"
    assert not by_tag["v0.4.1"].usable and not by_tag["v0.4.2"].usable


def test_the_wheel_must_belong_to_the_release_version(gh):
    rel = gh.add_release("0.4.0")
    rel["assets"][0]["name"] = "agflow-0.3.0-py3-none-any.whl"  # a wheel of another version attached to this tag
    assert feed(gh).list_releases().releases[0].wheel is None


def test_select_takes_the_newest_installable_release_of_the_channel(gh):
    gh.add_release("0.3.0")
    gh.add_release("0.4.0rc1", prerelease=True)
    gh.add_release("0.3.5")
    gh.add_release("0.9.0", sums=False)  # newest, but cannot be verified
    releases = feed(gh).list_releases().releases
    assert rf.select(releases, include_prereleases=False).tag == "v0.3.5"
    assert rf.select(releases, include_prereleases=True).tag == "v0.4.0rc1"
    assert rf.select([], include_prereleases=True) is None
    assert rf.select(releases[:0], include_prereleases=False) is None


def test_github_digest_is_kept_when_reported(gh):
    gh.add_release("0.4.0", digest=True)
    wheel = feed(gh).list_releases().releases[0].wheel
    assert wheel.digest == "sha256:" + hashlib.sha256(b"wheel-bytes").hexdigest()


@pytest.mark.parametrize("text, expected", [
    (f"{'a' * 64}  agflow-0.4.0-py3-none-any.whl\n", {"agflow-0.4.0-py3-none-any.whl": "a" * 64}),
    (f"{'B' * 64} *agflow.whl", {"agflow.whl": "b" * 64}),
    (f"{'c' * 63}  short\nnot a sum line\n\n{'d' * 64}   spaced name.whl  \n", {"spaced name.whl": "d" * 64}),
    ("", {}),
])
def test_sha256sums_parsing(text, expected):
    assert rf.parse_sums(text) == expected


def test_release_json_adds_knowledge_and_a_broken_one_adds_nothing():
    meta = rf.parse_meta('{"version": "0.4.0", "min_antigravity": "1.3.0", "tested_antigravity": ["1.3.3"], '
                         '"python": ">=3.10", "breaking": true, "notes": "n", "future_key": 1}')
    assert meta.min_antigravity == (1, 3, 0) and meta.tested_antigravity == ("1.3.3",) and meta.breaking
    assert meta.extra == {"future_key": 1}
    for broken in ("", "{nope", "[1]", '"str"'):
        assert rf.parse_meta(broken) == rf.Meta()


# --- network: success paths -----------------------------------------------------------------------


def test_conditional_requests_use_the_etag(gh):
    gh.add_release("0.4.0")
    first = feed(gh).list_releases()
    assert first.etag and not first.not_modified and len(first.releases) == 1
    again = feed(gh).list_releases(etag=first.etag)
    assert again.not_modified and again.releases == ()
    gh.add_release("0.4.1")  # the feed changed: the old ETag no longer matches
    assert len(feed(gh).list_releases(etag=first.etag).releases) == 2


def test_download_streams_to_disk_and_reports_the_hash_and_size(gh, tmp_path):
    gh.add_release("0.4.0", wheel=b"x" * 200_000)
    asset = feed(gh).list_releases().releases[0].wheel
    path, digest, size = feed(gh).download(asset, tmp_path / "dl" / asset.name)
    assert path.read_bytes() == b"x" * 200_000 and size == 200_000
    assert digest == hashlib.sha256(b"x" * 200_000).hexdigest()
    assert not list((tmp_path / "dl").glob("*.part"))


def test_checksum_file_is_fetched_as_text(gh):
    gh.add_release("0.4.0")
    release = feed(gh).list_releases().releases[0]
    assert rf.parse_sums(feed(gh).fetch_text(release.asset("SHA256SUMS"))) == {
        "agflow-0.4.0-py3-none-any.whl": hashlib.sha256(b"wheel-bytes").hexdigest()}


# --- network: every way it can go wrong becomes a plain FeedError ---------------------------------


@pytest.mark.parametrize("mode, words", [
    ("rate_limited", "rate limit"), ("not_found", "nothing is published"), ("server_error", "HTTP 500"),
    ("bad_json", "not JSON"), ("not_a_list", "not a list"), ("huge", "larger than"),
])
def test_feed_failures_are_explained(gh, mode, words):
    gh.mode = mode
    with pytest.raises(rf.FeedError, match=words):
        feed(gh).list_releases()


def test_an_unreachable_or_hanging_server_is_an_error_not_a_hang(gh):
    port_closed = rf.Feed(REPO, api="http://127.0.0.1:9", allow_loopback=True, timeout=1.0)
    with pytest.raises(rf.FeedError, match="could not reach GitHub"):
        port_closed.list_releases()
    gh.mode = "hang"
    with pytest.raises(rf.FeedError, match="could not reach GitHub"):
        feed(gh, timeout=0.4).list_releases()


def test_a_download_over_the_size_cap_is_refused_and_leaves_no_partial_file(gh, tmp_path):
    gh.add_release("0.4.0", wheel=b"y" * 5000)
    asset = feed(gh).list_releases().releases[0].wheel
    with pytest.raises(rf.FeedError, match="larger than 1000"):
        feed(gh).download(asset, tmp_path / "w.whl", max_bytes=1000)
    assert not (tmp_path / "w.whl").exists() and not list(tmp_path.glob("*.part"))


# --- the feed is pinned ---------------------------------------------------------------------------


@pytest.mark.parametrize("api, loopback", [
    ("http://api.github.com", False), ("https://evil.example", False), ("http://127.0.0.1:1", False),
    ("ftp://api.github.com", False),
])
def test_only_github_is_a_valid_feed_address(api, loopback):
    with pytest.raises(rf.FeedError, match="must be https://api.github.com"):
        rf.Feed(REPO, api=api, allow_loopback=loopback)


@pytest.mark.parametrize("repo", ["", "nope", "a/b/c", "o/r; rm -rf", "../x", "o/ r"])
def test_the_repository_must_be_owner_slash_name(repo):
    with pytest.raises(rf.FeedError, match="owner/name"):
        rf.Feed(repo)


def test_assets_are_only_fetched_from_allowed_hosts(gh):
    gh.add_release("0.4.0")
    evil = rf.Asset("agflow-0.4.0-py3-none-any.whl", "https://evil.example/agflow.whl")
    with pytest.raises(rf.FeedError, match="not a GitHub host"):
        feed(gh).fetch_text(evil)
    http_asset = rf.Asset("x", "http://api.github.com/x")
    with pytest.raises(rf.FeedError, match="not a GitHub host"):
        rf.Feed(REPO).fetch_text(http_asset)


def test_a_redirect_to_a_foreign_host_is_refused(gh):
    gh.add_release("0.4.0")
    path = next(p for p in gh.files if p.endswith("SHA256SUMS"))
    gh.redirect[path] = "https://evil.example/steal"
    with pytest.raises(rf.FeedError, match="not a GitHub host"):
        feed(gh).fetch_text(feed(gh).list_releases().releases[0].asset("SHA256SUMS"))


def test_a_redirect_from_https_to_http_is_refused():
    handler = rf._SafeRedirect(rf.GITHUB_ASSET_HOSTS, allow_loopback=False)
    req = urllib.request.Request("https://github.com/o/r/releases/download/v1/x")
    with pytest.raises(rf.FeedError, match="https to http"):
        handler.redirect_request(req, None, 302, "Found", {}, "http://objects.githubusercontent.com/x")


# --- credentials ----------------------------------------------------------------------------------


def test_no_ambient_token_is_ever_used(gh, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-secret")
    monkeypatch.setenv("GH_TOKEN", "ambient-secret-2")
    monkeypatch.delenv(rf.TOKEN_ENV, raising=False)
    gh.add_release("0.4.0")
    rf.Feed(REPO, api=gh.base, allow_loopback=True).list_releases()
    assert all("authorization" not in r["headers"] for r in gh.seen)


def test_the_explicit_token_is_sent_to_the_api_but_never_to_another_host_after_a_redirect(gh):
    other = FakeGitHub().start()
    try:
        gh.add_release("0.4.0")
        path = next(p for p in gh.files if p.endswith("SHA256SUMS"))
        other.files["/dl/final"] = b"abc  agflow-0.4.0-py3-none-any.whl\n"
        gh.redirect[path] = f"{other.base}/dl/final"
        f = rf.Feed(REPO, api=gh.base, allow_loopback=True, token="s3cret")
        release = f.list_releases().releases[0]
        assert gh.seen[0]["headers"].get("authorization") == "Bearer s3cret"
        f.fetch_text(release.asset("SHA256SUMS"))
        landed = [r for r in other.seen if r["path"] == "/dl/final"]
        assert landed and all("authorization" not in r["headers"] for r in landed), "the token followed the redirect"
    finally:
        other.stop()


def test_a_token_never_appears_in_an_error_message(gh):
    gh.mode = "server_error"
    with pytest.raises(rf.FeedError) as err:
        rf.Feed(REPO, api=gh.base, allow_loopback=True, token="s3cret").list_releases()
    assert "s3cret" not in str(err.value)


def test_version_objects_come_through_intact(gh):
    gh.add_release("0.10.0")
    gh.add_release("0.9.0")
    releases = feed(gh).list_releases().releases
    newest = max(releases, key=lambda r: r.version)
    assert newest.tag == "v0.10.0" and newest.version == versions.parse("0.10.0")
