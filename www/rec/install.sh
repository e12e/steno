#!/bin/sh
# Universal zero-Python installer for Steno (macOS, Linux amd64/arm64, & Android Termux aarch64-linux-android)
# Steno - a modern annalist for your meetings - an automaton stenographer.
#
# Usage:
#   curl -LsSf https://hypertekst.net/steno/install.sh | sh
#   or locally:
#   sh install.sh

set -eu

IS_TERMUX=0
if [ -n "${TERMUX_VERSION:-}" ] || [ -d "/data/data/com.termux/files/usr" ] || [ "$(uname -o 2>/dev/null || true)" = "Android" ]; then
    IS_TERMUX=1
fi

DEFAULT_BIN_DIR="$HOME/.local/bin"
if [ "$IS_TERMUX" = "1" ] && [ -n "${PREFIX:-}" ] && [ -d "$PREFIX/bin" ]; then
    DEFAULT_BIN_DIR="$PREFIX/bin"
fi

STENO_URL="${STENO_URL:-https://hypertekst.net/steno/steno.py}"
INSTALL_DIR="${STENO_INSTALL_DIR:-$HOME/.local/share/steno}"
BIN_DIR="${STENO_BIN_DIR:-$DEFAULT_BIN_DIR}"
SOURCE_FILE="${STENO_SOURCE_FILE:-}"
SKIP_UV_WARMUP="${STENO_SKIP_UV_WARMUP:-0}"

echo "==> Installing Steno — a modern annalist for your meetings — an automaton stenographer"

mkdir -p "$INSTALL_DIR" "$BIN_DIR"

TARGET_SCRIPT="$INSTALL_DIR/steno.py"

if [ -n "$SOURCE_FILE" ] && [ -f "$SOURCE_FILE" ]; then
    cp "$SOURCE_FILE" "$TARGET_SCRIPT"
elif [ -f "$(dirname "$0")/steno.py" ]; then
    cp "$(dirname "$0")/steno.py" "$TARGET_SCRIPT"
else
    echo "==> Downloading steno.py from $STENO_URL ..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "$STENO_URL" -o "$TARGET_SCRIPT"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$TARGET_SCRIPT" "$STENO_URL"
    else
        echo "Error: Neither curl nor wget is available to download steno.py." >&2
        exit 1
    fi
fi

chmod 0755 "$TARGET_SCRIPT"

# Create the `steno` CLI / GUI launcher in BIN_DIR (compatible with POSIX /bin/sh and Android Termux $PREFIX/bin/sh)
LAUNCHER="$BIN_DIR/steno"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
SH_BIN="/bin/sh"
if [ -n "\${PREFIX:-}" ] && [ -x "\$PREFIX/bin/sh" ]; then SH_BIN="\$PREFIX/bin/sh"
elif [ -x "/data/data/com.termux/files/usr/bin/sh" ]; then SH_BIN="/data/data/com.termux/files/usr/bin/sh"
elif [ ! -x "\$SH_BIN" ] && command -v sh >/dev/null 2>&1; then SH_BIN="\$(command -v sh)"; fi
exec "\$SH_BIN" "$TARGET_SCRIPT" "\$@"
EOF
chmod 0755 "$LAUNCHER"

if [ "$SKIP_UV_WARMUP" != "1" ]; then
    export PATH="${PREFIX:-/data/data/com.termux/files/usr}/bin:$BIN_DIR:$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
    if [ "$IS_TERMUX" = "1" ]; then
        if ! command -v python3 >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1; then
            echo "==> Installing native Termux packages (python, ffmpeg)..."
            if command -v pkg >/dev/null 2>&1; then
                pkg install -y python ffmpeg || true
            elif command -v apt-get >/dev/null 2>&1; then
                apt-get update -y && apt-get install -y python ffmpeg || true
            fi
        fi
        if command -v python3 >/dev/null 2>&1; then
            python3 "$TARGET_SCRIPT" --self-test >/dev/null 2>&1 || true
        fi
    else
        if ! command -v uv >/dev/null 2>&1; then
            echo "==> Installing standalone uv runtime manager (no pre-installed Python required)..."
            if [ -f /etc/alpine-release ] && [ ! -f /usr/lib/libstdc++.so.6 ] && command -v apk >/dev/null 2>&1; then
                apk add --no-cache libstdc++ >/dev/null 2>&1 || true
            fi
            if command -v curl >/dev/null 2>&1; then
                curl -LsSf https://astral.sh/uv/install.sh | sh
            elif command -v wget >/dev/null 2>&1; then
                wget -qO- https://astral.sh/uv/install.sh | sh
            fi
            export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        fi
        if command -v uv >/dev/null 2>&1; then
            echo "==> Provisioning standalone Python 3.12 runtime and dependencies..."
            uv run --python 3.12 --script "$TARGET_SCRIPT" --self-test >/dev/null 2>&1 || true
        fi
    fi
fi

echo "==> Installed Steno to: $LAUNCHER"
echo "    Run GUI:  $LAUNCHER"
echo "    Run CLI:  $LAUNCHER --ui-lang en --help"
