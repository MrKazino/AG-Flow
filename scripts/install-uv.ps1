# Installs uv from a pinned, checksum-verified release installer. Used by easy-installer.bat (Windows half).
#
# The old bootstrap ran `irm https://astral.sh/uv/install.ps1 | iex`: whatever the CDN served that day was executed
# unseen. This script downloads a specific release's installer to a file, checks its SHA-256 against
# scripts\uv-pin.env, and only then runs it. A mismatch aborts without running anything.
#
# Environment (for CI and one-off use):
#   AGFLOW_UV_VERSION            use another uv release; requires AGFLOW_UV_INSTALLER_SHA256
#   AGFLOW_UV_INSTALLER_SHA256   expected SHA-256 of that release's uv-installer.ps1
#   AGFLOW_UV_INSTALLER_URL      download from here instead (used by the tests)
$ErrorActionPreference = 'Stop'

function Read-Pin([string]$Name) {
    $line = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'uv-pin.env') |
        Where-Object { $_ -like "$Name=*" } | Select-Object -First 1
    if (-not $line) { throw "uv-pin.env has no $Name" }
    return $line.Substring($Name.Length + 1).Trim()
}

function Fail([string]$Message) {
    [Console]::Error.WriteLine($Message)
    exit 1
}

$pinnedVersion = Read-Pin 'UV_VERSION'
$version = if ($env:AGFLOW_UV_VERSION) { $env:AGFLOW_UV_VERSION } else { $pinnedVersion }
$expected = $env:AGFLOW_UV_INSTALLER_SHA256
if (-not $expected -and $version -eq $pinnedVersion) { $expected = Read-Pin 'UV_PS1_SHA256' }
if (-not $expected) {
    Fail ("error: AGFLOW_UV_VERSION=$version is not the pinned uv $pinnedVersion, so the SHA-256 of that release's " +
          "uv-installer.ps1 must be given as AGFLOW_UV_INSTALLER_SHA256 (or update scripts\uv-pin.env).")
}
$expected = $expected.ToLowerInvariant()
$url = if ($env:AGFLOW_UV_INSTALLER_URL) { $env:AGFLOW_UV_INSTALLER_URL }
       else { "https://github.com/astral-sh/uv/releases/download/$version/uv-installer.ps1" }

# Windows PowerShell 5.1 defaults to protocols GitHub no longer accepts
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$tmp = Join-Path ([IO.Path]::GetTempPath()) ('agflow-uv-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
$code = 0
try {
    $file = Join-Path $tmp 'uv-installer.ps1'
    Write-Host "Downloading the uv $version installer ($url)"
    try {
        Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing
    } catch {
        [Console]::Error.WriteLine("error: could not download ${url}: $($_.Exception.Message)")
        $code = 1
    }
    if ($code -eq 0) {
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            [Console]::Error.WriteLine(("error: the uv installer does not match its pinned checksum, so it was NOT run.`n" +
                "  version : $version`n  expected: $expected`n  actual  : $actual`n" +
                "  If you pinned a different version on purpose, set AGFLOW_UV_INSTALLER_SHA256 to its digest."))
            $code = 1
        }
    }
    if ($code -eq 0) {
        Write-Host 'Checksum verified; running the installer.'
        & powershell -NoProfile -ExecutionPolicy Bypass -File $file
        $code = $LASTEXITCODE
    }
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
exit $code
