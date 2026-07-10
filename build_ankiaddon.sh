#!/usr/bin/env bash
#
# Build a distributable .ankiaddon for the "AI Language Tutor" add-on.
#
# Uses an explicit ALLOWLIST (never a blacklist) so only vetted files ever enter
# the archive.
#
# The archive stores the add-on's files at its ROOT (no wrapping folder), as
# AnkiWeb requires. Upload the resulting file at
# https://ankiweb.net/shared/addons/
#
# Notes:
#   - manifest.json's `package`/`name` are used for direct (non-AnkiWeb)
#     distribution; AnkiWeb reads only `conflicts` from it and assigns the
#     package name itself. Shipping it is harmless and handy for GitHub installs.
#   - meta.json is intentionally omitted: Anki regenerates it on install, and
#     ours holds personal config.
#   - The displayed version comes from manifest.json's `human_version` key
#     (the key Anki actually reads and shows in Tools -> Add-ons).

set -euo pipefail

# Resolve repo root to this script's location so it works from any CWD.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BUILD_DIR="$ROOT/build"
STAGE_DIR="$BUILD_DIR/stage"

# --- Allowlist: the ONLY things that go into the add-on -----------------------
FILES=(
  "__init__.py"
  "reviewer.py"
  "providers.py"
  "provider_models.py"
  "config_dialog.py"
  "conversations.py"
  "config.json"
  "config.md"
  "manifest.json"
  "LICENSE"
)
DIRS=(
  "prompts"   # __init__.py + the four .j2 templates
  "web"       # ai_review*.css / ai_review.js
)

# --- Version (read from manifest.json) ----------------------------------------
# Like PACKAGE/NAME below, fail loudly on a broken manifest.json rather than
# silently building a "0.0.0" archive.
VERSION="$(python3 -c "
import json
m = json.load(open('manifest.json'))
v = m.get('human_version') or m.get('version')
if not v:
    raise SystemExit('manifest.json has no human_version/version')
print(v)
")"
PACKAGE="$(python3 -c "import json; print(json.load(open('manifest.json'))['package'])")"
NAME="$(python3 -c "import json; print(json.load(open('manifest.json'))['name'])")"
# Output filename is a slug of the display name (folder/package stays $PACKAGE).
SLUG="$(printf '%s' "$NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')"
OUT="$ROOT/${SLUG}-${VERSION}.ankiaddon"

echo ">> Building ${NAME} (package: ${PACKAGE}) v${VERSION}"

# --- Stage --------------------------------------------------------------------
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

for f in "${FILES[@]}"; do
  if [[ ! -e "$f" ]]; then
    echo "!! missing required file: $f" >&2
    exit 1
  fi
  cp "$f" "$STAGE_DIR/"
done

for d in "${DIRS[@]}"; do
  if [[ ! -d "$d" ]]; then
    echo "!! missing required dir: $d" >&2
    exit 1
  fi
  cp -R "$d" "$STAGE_DIR/"
done

# --- Scrub anything stray (defense in depth) ----------------------------------
# Drop compiled Python and EVERY hidden entry (.DS_Store, editor swap files,
# etc.) so nothing beginning with "." can ride along inside a copied dir.
find "$STAGE_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGE_DIR" -name '*.pyc' -delete
find "$STAGE_DIR" -depth -name '.*' -exec rm -rf {} +

# --- Zip with contents at the archive root ------------------------------------
rm -f "$OUT"
( cd "$STAGE_DIR" && zip -r -X "$OUT" . >/dev/null )

echo ">> Wrote $OUT"
echo ">> Contents:"
unzip -l "$OUT"
