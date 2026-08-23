#!/usr/bin/env bash
#
# Set up the environment and launch the Signature Mouse Signer.
#
# Creates a .venv on first run, installs requirements.txt into it, then starts
# the app. Re-runs reuse the existing venv and only reinstall when
# requirements.txt has changed since the last install.
#
# Usage:
#   ./run.sh              # set up if needed, then run
#   ./run.sh --recreate   # rebuild the virtual environment from scratch
#   ./run.sh --skip-install
#
# Works under Linux/macOS shells and under Git Bash / MSYS / WSL on Windows.
# Note: replay and encrypted storage use Windows-only APIs (SendInput, DPAPI,
# Windows Hello). On Linux the GUI and capture run, but replay is unavailable.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT/.venv"
REQUIREMENTS="$ROOT/requirements.txt"
STAMP="$VENV_DIR/.requirements.sha256"

RECREATE=0
SKIP_INSTALL=0
ARGS=()

for arg in "$@"; do
    case "$arg" in
        --recreate)     RECREATE=1 ;;
        --skip-install) SKIP_INSTALL=1 ;;
        *)              ARGS+=("$arg") ;;
    esac
done

info()  { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()    { printf '\033[32m==> %s\033[0m\n' "$1"; }
die()   { printf '\033[31merror: %s\033[0m\n' "$1" >&2; exit 1; }

# Git Bash on Windows puts the interpreter in Scripts/ and names it python.exe.
if [[ -x "$VENV_DIR/bin/python" ]]; then
    VENV_PYTHON="$VENV_DIR/bin/python"
elif [[ -x "$VENV_DIR/Scripts/python.exe" ]]; then
    VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
else
    VENV_PYTHON=""
fi

find_python() {
    for candidate in python3 python py; do
        if command -v "$candidate" >/dev/null 2>&1; then
            # The Microsoft Store alias stub on Windows exits without running.
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1   # macOS
    fi
}

if [[ $RECREATE -eq 1 && -d "$VENV_DIR" ]]; then
    info "Removing existing .venv"
    rm -rf "$VENV_DIR"
    VENV_PYTHON=""
fi

if [[ -z "$VENV_PYTHON" ]]; then
    PYTHON="$(find_python)" || die "No Python 3.9+ found on PATH. Install it and re-run."
    info "Creating virtual environment in .venv"
    "$PYTHON" -m venv "$VENV_DIR"
    if [[ -x "$VENV_DIR/bin/python" ]]; then
        VENV_PYTHON="$VENV_DIR/bin/python"
    else
        VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
    fi
fi

if [[ $SKIP_INSTALL -eq 0 ]]; then
    hash="$(sha256_of "$REQUIREMENTS")"
    previous="$([[ -f "$STAMP" ]] && cat "$STAMP" || echo '')"

    if [[ "$hash" != "$previous" ]]; then
        info "Installing dependencies"
        # No pip self-upgrade here: on Windows, replacing pip while it runs
        # leaves a broken '~ip' directory in site-packages.
        "$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$REQUIREMENTS"
        printf '%s\n' "$hash" > "$STAMP"
    else
        info "Dependencies up to date"
    fi
fi

ok "Starting Signature Mouse Signer"

# Anchor imports on the repo root so the script works from any working directory.
# Git Bash hands Windows-native Python a POSIX path, so translate it there.
PYTHONPATH_ROOT="$ROOT"
SEP=':'
if [[ "$VENV_PYTHON" == *.exe ]]; then
    SEP=';'   # native Windows Python splits PYTHONPATH on ';'
    command -v cygpath >/dev/null 2>&1 && PYTHONPATH_ROOT="$(cygpath -w "$ROOT")"
fi
export PYTHONPATH="${PYTHONPATH_ROOT}${PYTHONPATH:+${SEP}${PYTHONPATH}}"

exec "$VENV_PYTHON" -m app ${ARGS[@]+"${ARGS[@]}"}
