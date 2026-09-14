#!/usr/bin/env bash
# reportfast build, from wherever you happen to be.
#
#   ./build.sh reports/foo.yaml                  # write it, probe every DataID
#   ./build.sh reports/foo.yaml --check-only     # probe; write no file
#   ./build.sh reports/foo.yaml --out /tmp/x.html --no-check
#
# A thin wrapper. Note what is NOT here: no `--publish` default, no alias that
# uploads, no env var that makes publishing automatic. If you want the report on
# a run you type `--publish`, and the tool prints the run id before it goes.
#
# Exit codes: 0 fine, 1 manifest wrong, 2 built and a DataID will not open,
# 3 an extra is missing, 4 nothing to work on.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: ${0##*/} <manifest.yaml> [build flags]" >&2
  exit 4
fi

if command -v reportfast >/dev/null 2>&1; then
  exec reportfast build "$@"
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
if [[ -x "$here/.venv/bin/reportfast" ]]; then
  exec "$here/.venv/bin/reportfast" build "$@"
fi
if command -v uv >/dev/null 2>&1 && [[ -f "$here/pyproject.toml" ]]; then
  exec uv run --project "$here" reportfast build "$@"
fi

echo "${0##*/}: no reportfast on PATH, no repo checkout found above this script." >&2
echo "  uv sync --extra manifest && uv run reportfast build $1" >&2
exit 3
