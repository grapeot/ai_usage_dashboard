#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
JSON_OUTPUT_FILE="$SCRIPT_DIR/token_usage_eink.json"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:$PATH"

if [ ! -d "$VENV_DIR" ]; then
  echo ".venv not found: $VENV_DIR" >&2
  exit 1
fi

cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
if [ -n "${AI_USAGE_OPENCODE_SKILL_PATH:-}" ]; then
  export PYTHONPATH="$AI_USAGE_OPENCODE_SKILL_PATH${PYTHONPATH:+:$PYTHONPATH}"
elif [ -d "$SCRIPT_DIR/../opencode_skill/src" ]; then
  export PYTHONPATH="$SCRIPT_DIR/../opencode_skill/src${PYTHONPATH:+:$PYTHONPATH}"
fi

python auto_usage.py -d 7
python auto_usage.py -d 30 --skip-desktop-chart

if [ ! -f "$JSON_OUTPUT_FILE" ]; then
  echo "Expected output missing: $JSON_OUTPUT_FILE" >&2
  exit 1
fi

echo "Updated local artifacts: $JSON_OUTPUT_FILE"
