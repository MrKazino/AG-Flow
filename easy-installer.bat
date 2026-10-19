:<<"::AGFLOW_SH"
@echo off
rem ===========================================================================================
rem  agflow easy installer - installs requirements, builds agflow from source, installs it.
rem
rem  This one file runs on every platform:
rem    Windows  : double-click it, or run   easy-installer.bat [options]
rem    Linux / macOS / WSL / BSD / Git Bash :   sh easy-installer.bat [options]
rem
rem  Options: --yes --test --no-sdk --no-plugin --profile NAME --editable --uninstall --help
rem
rem  How it works: the first line is a label for Windows and a heredoc for sh, so cmd.exe runs
rem  this batch block and sh skips it. Both halves only make sure "uv" is available and then
rem  hand over to scripts/easy_install.py, which does the real work the same way on every OS.
rem ===========================================================================================
setlocal EnableExtensions
title agflow easy installer
cd /d "%~dp0"

set "AGFLOW_ARGS= %* "
set "AGFLOW_YES="
if not "%AGFLOW_ARGS: --yes =%"=="%AGFLOW_ARGS%" set "AGFLOW_YES=1"
if not "%AGFLOW_ARGS: -y =%"=="%AGFLOW_ARGS%" set "AGFLOW_YES=1"
set "AGFLOW_PAUSE="
rem pause at the end only when started by double-click (cmd /c "...easy-installer.bat")
echo %cmdcmdline% | find /i "%~nx0" >nul
if not errorlevel 1 set "AGFLOW_PAUSE=1"

rem --- 1. find uv ---------------------------------------------------------------------------
set "UV="
for /f "delims=" %%i in ('where uv 2^>nul') do if not defined UV set "UV=%%i"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV=%LOCALAPPDATA%\Programs\uv\uv.exe"

rem --- 2. install uv if missing: a pinned release installer, checksum-verified before it runs --------------
rem     (scripts\install-uv.ps1 reads scripts\uv-pin.env; nothing is piped into a shell)
if not defined UV echo uv (the Python package manager from Astral) is required and was not found.
set "AGFLOW_OK=y"
if not defined UV if not defined AGFLOW_YES set /p "AGFLOW_OK=Install uv now from https://astral.sh/uv ? [Y/n] "
if /i "%AGFLOW_OK%"=="n" (echo Aborted. Install uv from https://docs.astral.sh/uv/ and run this again. & exit /b 1)
if /i "%AGFLOW_OK%"=="no" (echo Aborted. Install uv from https://docs.astral.sh/uv/ and run this again. & exit /b 1)
if not defined UV powershell -NoProfile -ExecutionPolicy ByPass -File "%~dp0scripts\install-uv.ps1"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV (echo error: uv could not be installed. See https://docs.astral.sh/uv/getting-started/installation/ & exit /b 1)

rem --- 3. hand over to the cross-platform installer (uv provides Python if needed) ------------------
set "AGFLOW_UV=%UV%"
"%UV%" run --no-project --python ">=3.10" "%~dp0scripts\easy_install.py" %*
set "AGFLOW_RC=%ERRORLEVEL%"
if defined AGFLOW_PAUSE if not defined AGFLOW_YES pause
exit /b %AGFLOW_RC%
::AGFLOW_SH
# ============================================================================================
#  POSIX sh part (Linux, macOS, WSL, BSD, Git Bash/MSYS on Windows):  sh easy-installer.bat
# ============================================================================================
set -eu
cd "$(dirname "$0")"

YES=""
for arg in "$@"; do
  case "$arg" in -y|--yes) YES=1 ;; esac
done

find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return 0; fi
  for candidate in "${XDG_BIN_HOME:-$HOME/.local/bin}/uv" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then echo "$candidate"; return 0; fi
  done
  return 1
}

# --- 1. find uv -----------------------------------------------------------------------------------
UV=$(find_uv || true)

# --- 2. install uv if missing: a pinned release installer, checksum-verified before it runs --------------
#        (scripts/install-uv.sh reads scripts/uv-pin.env; nothing is piped into a shell)
if [ -z "$UV" ]; then
  echo "uv (the Python package manager from Astral) is required and was not found."
  if [ -z "$YES" ]; then
    if [ ! -t 0 ]; then
      echo "error: uv is required and installing it needs your approval, but this session is not interactive." >&2
      echo "Re-run with --yes to install the pinned, checksum-verified uv, or install uv yourself:" >&2
      echo "https://docs.astral.sh/uv/getting-started/installation/" >&2
      exit 1
    fi
    printf "Install uv now from https://astral.sh/uv ? [Y/n] "
    read -r answer || answer=""
    case "$answer" in
      n|N|no|NO) echo "Aborted. Install uv from https://docs.astral.sh/uv/ and run this again."; exit 1 ;;
    esac
  fi
  sh scripts/install-uv.sh
  UV=$(find_uv || true)
  if [ -z "$UV" ]; then
    echo "error: uv could not be installed. See https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
  fi
fi

# --- 3. hand over to the cross-platform installer (uv provides Python if needed) -----------------------
AGFLOW_UV="$UV"
export AGFLOW_UV
exec "$UV" run --no-project --python ">=3.10" scripts/easy_install.py "$@"
