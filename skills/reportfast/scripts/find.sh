#!/usr/bin/env bash
# reportfast find, from wherever you happen to be.
#
#   ./find.sh <run-id>                        # files at the root of its artifacts/
#   ./find.sh <run-id> --path tile_masks      # one artifact subdirectory
#   ./find.sh <run-id> -r --slides            # every slide, descending
#   ./find.sh <run-id> --data-ids             # one DataID per line, for piping
#
# Lists a run you already have. It does not search MLflow for runs -- nothing in
# this tool does -- so a run id comes from the UI or from a person. Requires the
# mlflow extra: `uv sync --extra mlflow`.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: ${0##*/} <run-id> [--path DIR] [-r] [--slides] [--data-ids]" >&2
  exit 4
fi

if command -v reportfast >/dev/null 2>&1; then
  exec reportfast find "$@"
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
if [[ -x "$here/.venv/bin/reportfast" ]]; then
  exec "$here/.venv/bin/reportfast" find "$@"
fi
if command -v uv >/dev/null 2>&1 && [[ -f "$here/pyproject.toml" ]]; then
  exec uv run --project "$here" reportfast find "$@"
fi

echo "${0##*/}: no reportfast on PATH, no repo checkout found above this script." >&2
echo "  uv sync --extra mlflow && uv run reportfast find $1" >&2
exit 3
