#!/bin/sh
# Installs uv from a pinned, checksum-verified release installer. Used by easy-installer.bat (POSIX half).
#
# The old bootstrap ran `curl https://astral.sh/uv/install.sh | sh`: whatever the CDN served that day was executed
# unseen. This script downloads a specific release's installer to a file, checks its SHA-256 against
# scripts/uv-pin.env, and only then runs it. A mismatch aborts without running anything.
#
# Environment (for CI and one-off use):
#   AGFLOW_UV_VERSION            use another uv release; requires AGFLOW_UV_INSTALLER_SHA256
#   AGFLOW_UV_INSTALLER_SHA256   expected SHA-256 of that release's uv-installer.sh
#   AGFLOW_UV_INSTALLER_URL      download from here instead (used by the tests; skips the https-only restriction)
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
pinfile="$here/uv-pin.env"

pin() { # pin KEY: its value from uv-pin.env (CR-tolerant, no eval)
  sed -n "s/^$1=//p" "$pinfile" | tr -d '\r' | head -n 1
}

sha256_of() { # sha256_of FILE: lowercase hex digest, using whichever tool exists
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d ' ' -f 1
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d ' ' -f 1
  elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 "$1" | sed 's/^.*= //'
  else
    echo "error: cannot verify the uv installer: none of sha256sum, shasum or openssl is available." >&2
    return 1
  fi
}

download() { # download URL DEST
  if command -v curl >/dev/null 2>&1; then
    if [ -n "${AGFLOW_UV_INSTALLER_URL:-}" ]; then curl -fsSL --retry 2 -o "$2" "$1"
    else curl -fsSL --retry 2 --proto '=https' --proto-redir '=https' -o "$2" "$1"; fi
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    echo "error: curl or wget is needed to install uv. See https://docs.astral.sh/uv/getting-started/installation/" >&2
    return 1
  fi
}

pinned_version=$(pin UV_VERSION)
version="${AGFLOW_UV_VERSION:-$pinned_version}"
expected="${AGFLOW_UV_INSTALLER_SHA256:-}"
if [ -z "$expected" ] && [ "$version" = "$pinned_version" ]; then
  expected=$(pin UV_SH_SHA256)
fi
if [ -z "$expected" ]; then
  echo "error: AGFLOW_UV_VERSION=$version is not the pinned uv $pinned_version, so the SHA-256 of that release's" >&2
  echo "uv-installer.sh must be given as AGFLOW_UV_INSTALLER_SHA256 (or update scripts/uv-pin.env)." >&2
  exit 1
fi
expected=$(printf '%s' "$expected" | tr 'A-F' 'a-f')
url="${AGFLOW_UV_INSTALLER_URL:-https://github.com/astral-sh/uv/releases/download/$version/uv-installer.sh}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
file="$tmp/uv-installer.sh"

echo "Downloading the uv $version installer ($url)"
download "$url" "$file" || { echo "error: could not download $url" >&2; exit 1; }
actual=$(sha256_of "$file") || exit 1
if [ "$actual" != "$expected" ]; then
  {
    echo "error: the uv installer does not match its pinned checksum, so it was NOT run."
    echo "  version : $version"
    echo "  expected: $expected"
    echo "  actual  : $actual"
    echo "  If you pinned a different version on purpose, set AGFLOW_UV_INSTALLER_SHA256 to its digest."
  } >&2
  exit 1
fi
echo "Checksum verified; running the installer."
sh "$file"
