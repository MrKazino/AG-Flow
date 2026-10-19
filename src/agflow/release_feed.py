"""Where AG-Flow updates come from: the GitHub Releases of one pinned repository, nothing else.

Trust model, in order of how much each step protects:

* **The source is pinned.** The feed is ``https://api.github.com`` for ``MrKazino/AG-Flow``; asset downloads are only
  followed to GitHub's own hosts. The name ``agflow`` is *not* registered on PyPI, so a package index is never
  consulted: anyone could register it.
* **Every asset is checked against ``SHA256SUMS``** published in the same release (and against the ``digest`` GitHub
  reports for the asset when it has one) before anything is installed; see :mod:`agflow.update`.
* **Credentials never travel by accident.** No ambient token is read; ``AGFLOW_GITHUB_TOKEN`` must be set on purpose,
  and the header is dropped on any redirect to another host. A redirect from https to http is refused.
* **Everything is bounded:** timeouts, a response size cap, a download size cap. A feed that misbehaves costs a
  :class:`FeedError` with a plain message, never a hang or a traceback.

The wire format is GitHub's documented "list releases" JSON; the only agflow-specific asset is ``release.json``
(compatibility metadata, :class:`Meta`). Stdlib only; ``allow_loopback`` exists so tests can run a fake server.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow import versions

DEFAULT_REPO = "MrKazino/AG-Flow"
DEFAULT_API = "https://api.github.com"
# hosts a release asset may be served from when the feed is GitHub itself
GITHUB_ASSET_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                      "release-assets.githubusercontent.com", "github-releases.githubusercontent.com")
TOKEN_ENV = "AGFLOW_GITHUB_TOKEN"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_TEXT_ASSET_BYTES = 256 * 1024
MAX_WHEEL_BYTES = 80 * 1024 * 1024
SUMS_NAME = "SHA256SUMS"
META_NAME = "release.json"
_WHEEL = re.compile(r"^agflow-(?P<version>[0-9][^-]*)-py3-none-any\.whl$")
_SUM_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S.*?)\s*$")


class FeedError(RuntimeError):
    """The release feed could not be used. The message is for the user and never contains a credential."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int = 0
    digest: str | None = None  # "sha256:<hex>" as reported by GitHub, when it reports one


@dataclass(frozen=True)
class Release:
    tag: str
    version: versions.Version
    prerelease: bool
    notes: str
    published_at: str
    assets: tuple[Asset, ...] = ()

    def asset(self, name: str) -> Asset | None:
        return next((a for a in self.assets if a.name == name), None)

    @property
    def wheel(self) -> Asset | None:
        for a in self.assets:
            m = _WHEEL.match(a.name)
            if m and versions.parse(m.group("version")) == self.version:
                return a
        return None

    @property
    def usable(self) -> bool:
        """A release can be installed only with its wheel *and* its checksum list."""
        return self.wheel is not None and self.asset(SUMS_NAME) is not None


@dataclass(frozen=True)
class Meta:
    """``release.json``: what a release declares about the environment it was tested against."""

    version: str = ""
    min_antigravity: tuple[int, ...] = ()
    tested_antigravity: tuple[str, ...] = ()
    python: str = ""
    breaking: bool = False
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedResult:
    releases: tuple[Release, ...]
    etag: str | None = None
    not_modified: bool = False


# --- parsing (pure) -------------------------------------------------------------------------------------------------


def parse_release(item: Any) -> Release | None:
    """A :class:`Release` from one entry of GitHub's list, or ``None`` for drafts and anything unreadable."""
    if not isinstance(item, dict) or item.get("draft"):
        return None
    tag = item.get("tag_name")
    version = versions.parse(tag)
    if not isinstance(tag, str) or version is None:
        return None
    assets = []
    for raw in item.get("assets") or []:
        if (isinstance(raw, dict) and isinstance(raw.get("name"), str)
                and isinstance(raw.get("browser_download_url"), str)):
            digest = raw.get("digest") if isinstance(raw.get("digest"), str) else None
            size = raw.get("size") if isinstance(raw.get("size"), int) else 0
            assets.append(Asset(raw["name"], raw["browser_download_url"], size, digest))
    return Release(tag, version, bool(item.get("prerelease")) or version.is_prerelease,
                   str(item.get("body") or "")[:20_000], str(item.get("published_at") or ""), tuple(assets))


def parse_sums(text: str) -> dict[str, str]:
    """``sha256sum`` output -> ``{file name: lowercase hex}``; lines that are not that format are ignored."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _SUM_LINE.match(line.strip())
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def parse_meta(text: str) -> Meta:
    """``release.json`` -> :class:`Meta`. A missing or damaged file is an empty Meta: it only *adds* knowledge."""
    try:
        data = json.loads(text)
    except ValueError:
        return Meta()
    if not isinstance(data, dict):
        return Meta()
    tested = data.get("tested_antigravity")
    known = {"version", "min_antigravity", "tested_antigravity", "python", "breaking", "notes"}
    return Meta(
        version=str(data.get("version") or ""),
        min_antigravity=versions.numeric_tuple(str(data.get("min_antigravity") or "")),
        tested_antigravity=tuple(str(t) for t in tested) if isinstance(tested, list) else (),
        python=str(data.get("python") or ""),
        breaking=bool(data.get("breaking")),
        notes=str(data.get("notes") or "")[:2000],
        extra={k: v for k, v in data.items() if k not in known})


def select(releases: Iterable[Release], *, include_prereleases: bool) -> Release | None:
    """The newest installable release of the channel (usable = has its wheel and its checksum list)."""
    candidates = [r for r in releases if r.usable and (include_prereleases or not r.prerelease)]
    return max(candidates, key=lambda r: r.version) if candidates else None


# --- network --------------------------------------------------------------------------------------------------------


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to allowed hosts, never from https to http, and never carry the token to another host."""

    def __init__(self, allowed: tuple[str, ...], allow_loopback: bool):
        self.allowed, self.allow_loopback = allowed, allow_loopback

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if old.scheme == "https" and new.scheme != "https":
            raise FeedError(f"refused a redirect from https to {new.scheme or 'an unknown scheme'}")
        if not _host_ok(new, self.allowed, self.allow_loopback):
            raise FeedError(f"refused a redirect to {new.hostname}: not a GitHub host")
        nxt = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nxt is not None and (old.hostname, old.port) != (new.hostname, new.port):
            for store in (nxt.headers, nxt.unredirected_hdrs):
                for key in [k for k in store if k.lower() == "authorization"]:
                    del store[key]
        return nxt


def _is_loopback(host: str | None) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def _host_ok(parts: urllib.parse.SplitResult, allowed: tuple[str, ...], allow_loopback: bool) -> bool:
    if parts.scheme == "https" and parts.hostname in allowed:
        return True
    return allow_loopback and parts.scheme in ("http", "https") and _is_loopback(parts.hostname)


class Feed:
    """The releases of one GitHub repository. ``api`` and ``allow_loopback`` exist for tests and mirrors."""

    def __init__(self, repo: str = DEFAULT_REPO, *, api: str = DEFAULT_API, token: str | None = None,
                 timeout: float = 10.0, allow_loopback: bool = False):
        owner, _, name = repo.partition("/")
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
                or owner in (".", "..") or name in (".", "..")):
            raise FeedError(f"{repo!r} is not an owner/name repository")
        self.repo, self.api, self.timeout = repo, api.rstrip("/"), timeout
        self.token = token if token is not None else os.environ.get(TOKEN_ENV)
        self.allow_loopback = allow_loopback
        self.allowed = GITHUB_ASSET_HOSTS  # downloads come from GitHub's own hosts, whatever the feed address is
        if not _host_ok(urllib.parse.urlsplit(self.api), ("api.github.com",), allow_loopback):
            raise FeedError(f"the feed address {api!r} must be https://api.github.com")
        self._opener = urllib.request.build_opener(_SafeRedirect(self.allowed, allow_loopback))

    # -- plumbing ----------------------------------------------------------------------------------------------------
    def _open(self, url: str, headers: dict[str, str]):  # type: ignore[no-untyped-def]
        parts = urllib.parse.urlsplit(url)
        if not _host_ok(parts, self.allowed, self.allow_loopback):
            raise FeedError(f"refused to contact {parts.hostname or url!r}: not a GitHub host")
        req = urllib.request.Request(url, headers={"User-Agent": "agflow-updater", **headers})
        if self.token and parts.hostname in ("api.github.com", urllib.parse.urlsplit(self.api).hostname):
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            return self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return exc
            remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
            if exc.code in (403, 429) and remaining == "0":
                raise FeedError("GitHub's rate limit for unauthenticated requests is used up; try again in an hour "
                                f"(or set {TOKEN_ENV})") from None
            if exc.code == 404:
                raise FeedError(f"{self.repo} has no such release data (HTTP 404): nothing is published yet, or the "
                                "repository is private") from None
            raise FeedError(f"GitHub answered HTTP {exc.code}") from None
        except FeedError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise FeedError(f"could not reach GitHub ({type(reason).__name__}: {reason})") from None

    def _read_limited(self, resp, limit: int) -> bytes:  # type: ignore[no-untyped-def]
        data = resp.read(limit + 1)
        if len(data) > limit:
            raise FeedError(f"the answer is larger than {limit} bytes: refused")
        return data

    # -- API ---------------------------------------------------------------------------------------------------------
    def list_releases(self, *, etag: str | None = None) -> FeedResult:
        headers = {"Accept": "application/vnd.github+json"}
        if etag:
            headers["If-None-Match"] = etag
        resp = self._open(f"{self.api}/repos/{self.repo}/releases?per_page=30", headers)
        if getattr(resp, "code", 200) == 304:
            return FeedResult((), etag, not_modified=True)
        with resp:
            body = self._read_limited(resp, MAX_JSON_BYTES)
            new_etag = resp.headers.get("ETag")
        try:
            data = json.loads(body)
        except ValueError:
            raise FeedError("GitHub's answer was not JSON") from None
        if not isinstance(data, list):
            raise FeedError("GitHub's answer was not a list of releases")
        releases = tuple(r for r in map(parse_release, data) if r is not None)
        return FeedResult(releases, new_etag)

    def fetch_text(self, asset: Asset) -> str:
        with self._open(asset.url, {"Accept": "application/octet-stream"}) as resp:
            return self._read_limited(resp, MAX_TEXT_ASSET_BYTES).decode("utf-8", errors="replace")

    def download(self, asset: Asset, dest: Path, *, max_bytes: int = MAX_WHEEL_BYTES) -> tuple[Path, str, int]:
        """Stream ``asset`` to ``dest`` (through a ``.part`` file); returns ``(dest, sha256, size)``."""
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        try:
            with self._open(asset.url, {"Accept": "application/octet-stream"}) as resp, part.open("wb") as out:
                while chunk := resp.read(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FeedError(f"{asset.name} is larger than {max_bytes} bytes: refused")
                    digest.update(chunk)
                    out.write(chunk)
            os.replace(part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return dest, digest.hexdigest(), size
