#!/bin/sh
# Run the daily news briefing pipeline.
#
# Secrets are read from the environment — export them first, or source a .env:
#   set -a; . ./.env; set +a
#   sh scripts/run.sh
#
# Any args are passed through to the job, e.g.:
#   sh scripts/run.sh --dry-run
#   sh scripts/run.sh --since-days 2 --no-push
set -e
REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO_DIR"
exec uv run python -m news.run "$@"
