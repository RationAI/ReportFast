#!/usr/bin/env bash
# reportfast plan, from wherever you happen to be.
#
#   ./plan.sh reports/foo.yaml            # the plan, as a person reads it
#   ./plan.sh reports/foo.yaml --json     # the plan, as an agent reads it
#
# A thin wrapper, on purpose: it finds the tool and gets out of the way. Nothing
# here is worth knowing about -- `reportfast plan` does the same thing, and the
# exit codes (0 fine, 1 manifest wrong, 4 nothing to work on) are its own.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: ${0##*/} <manifest.yaml> [plan flags]" >&2
  exit 4
fi

if command -v reportfast >/dev/null 2>&1; then
  exec reportfast plan "$@"
fi

# No console script on PATH: run from the repo instead, through uv, so the
# project's own pinned environment answers.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
if [[ -x "$here/.venv/bin/reportfast" ]]; then
  exec "$here/.venv/bin/reportfast" plan "$@"
fi
if command -v uv >/dev/null 2>&1 && [[ -f "$here/pyproject.toml" ]]; then
  exec uv run --project "$here" reportfast plan "$@"
fi

echo "${0##*/}: no reportfast on PATH, no repo checkout found above this script." >&2
echo "  uv sync --extra manifest && uv run reportfast plan $1" >&2
exit 3
