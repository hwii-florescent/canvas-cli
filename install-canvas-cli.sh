#!/bin/sh
set -eu

BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/canvas"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/canvas-cli"
VENV_DIR="$APP_DIR/venv"
PY_FILE="$APP_DIR/canvas.py"
BROWSER_DIR="$APP_DIR/ms-playwright"
STAMP="$APP_DIR/.installed-v2"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE="$SCRIPT_DIR/canvas"

if [ ! -f "$SOURCE" ] || [ ! -r "$SOURCE" ]; then
  echo "Canvas source is missing or unreadable: $SOURCE" >&2
  exit 1
fi

mkdir -p "$BIN_DIR" "$APP_DIR"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Setting up private Canvas CLI environment..." >&2
  python3 -m venv "$VENV_DIR"
fi

if [ ! -f "$STAMP" ]; then
  echo "Installing private dependencies..." >&2
  "$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV_DIR/bin/python" -m pip install playwright >/dev/null

  echo "Installing private Chromium runtime..." >&2
  PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" \
    "$VENV_DIR/bin/python" -m playwright install chromium

  touch "$STAMP"
fi

SOURCE_TMP=""
WRAPPER_TMP=""
cleanup() {
  if [ -n "$SOURCE_TMP" ]; then
    rm -f "$SOURCE_TMP"
  fi
  if [ -n "$WRAPPER_TMP" ]; then
    rm -f "$WRAPPER_TMP"
  fi
}
trap cleanup EXIT HUP INT TERM

umask 077
SOURCE_TMP=$(mktemp "$APP_DIR/.canvas.py.XXXXXX")
cp "$SOURCE" "$SOURCE_TMP"
chmod 0700 "$SOURCE_TMP"
mv -f "$SOURCE_TMP" "$PY_FILE"
SOURCE_TMP=""

WRAPPER_TMP=$(mktemp "$BIN_DIR/.canvas.XXXXXX")
cat > "$WRAPPER_TMP" <<'WRAPPER'
#!/bin/sh
set -eu

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/canvas-cli"
VENV_DIR="$APP_DIR/venv"
PY_FILE="$APP_DIR/canvas.py"
BROWSER_DIR="$APP_DIR/ms-playwright"

export PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR"
exec "$VENV_DIR/bin/python" "$PY_FILE" "$@"
WRAPPER
chmod 0755 "$WRAPPER_TMP"
mv -f "$WRAPPER_TMP" "$BIN"
WRAPPER_TMP=""

case ":${PATH:-}:" in
  *":$BIN_DIR:"*) ;;
  *)
    if [ -f "$HOME/.zshrc" ]; then
      case "$(cat "$HOME/.zshrc")" in
        *'HOME/.local/bin'*) ;;
        *) printf '%s\n' 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc" ;;
      esac
    else
      printf '%s\n' 'export PATH="$HOME/.local/bin:$PATH"' > "$HOME/.zshrc"
    fi
    ;;
esac

echo "Installed self-contained canvas CLI to:"
echo "  $BIN"
echo
echo "Run:"
echo "  source ~/.zshrc"
echo "  hash -r"
echo "  canvas"
