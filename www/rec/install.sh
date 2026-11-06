#!/bin/sh
# Universal zero-Python installer for Steno (macOS & Linux)
# Steno - a modern annalist for your meetings - an automaton stenographer.
#
# Usage:
#   curl -LsSf https://hypertekst.net/steno/install.sh | sh
#   or locally:
#   sh install.sh

set -eu

STENO_URL="${STENO_URL:-https://hypertekst.net/steno/steno.py}"
INSTALL_DIR="${STENO_INSTALL_DIR:-$HOME/.local/share/steno}"
BIN_DIR="${STENO_BIN_DIR:-$HOME/.local/bin}"
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

# Create the `steno` CLI / GUI launcher in BIN_DIR (runs via /bin/sh without requiring system python3)
LAUNCHER="$BIN_DIR/steno"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
exec /bin/sh "$TARGET_SCRIPT" "\$@"
EOF
chmod 0755 "$LAUNCHER"

if [ "$SKIP_UV_WARMUP" != "1" ]; then
    export PATH="$BIN_DIR:$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
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

echo "==> Installed Steno to: $LAUNCHER"
echo "    Run GUI:  $LAUNCHER"
echo "    Run CLI:  $LAUNCHER --ui-lang en --help"
