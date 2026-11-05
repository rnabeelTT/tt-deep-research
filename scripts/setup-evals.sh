#!/usr/bin/env bash
# Everything the evals need on a new machine: the stack, the suites, the benchmark documents and
# their search indexes. Safe to re-run; each step skips what's already there.
#
#   scripts/setup-evals.sh                  # embed the documents here (BrowseComp-Plus takes hours)
#   scripts/setup-evals.sh ~/snapshots      # restore indexes saved on another machine instead:
#                                           #   there: uv run ttdr corpus snapshot --out ~/snapshots
#
# Needs: a QB2 with tt and tt-model, Docker, uv, and a Hugging Face login with access to the gated
# datasets (HLE; `hf auth login`). Benchmark data stays under data/ (gitignored, internal use only).
set -euo pipefail
cd "$(dirname "$0")/.."
RECIPE=${TTDR_RECIPE:-recipes/ornith-35b-cohost.toml}
SNAPSHOTS=${1:-}

step() { printf '\n== %s\n' "$*"; }

step "Python environment"
uv sync

step "Hugging Face login (gated datasets)"
uv run hf auth whoami >/dev/null 2>&1 || { echo "Run: uv run hf auth login"; exit 1; }

step "Models, data services and the app ($RECIPE; a cold model start takes 10-15 minutes)"
uv run ttdr up --recipe "$RECIPE"

step "Eval suites → data/suites"
uv run ttdr eval build

step "Benchmark documents → data/corpus (PDFs and pages that citations and read_page open)"
for c in financebench unidoc browsecomp-plus; do
    [ -n "$(ls -A "data/corpus/$c" 2>/dev/null)" ] && echo "$c: already fetched" || uv run ttdr corpus fetch "$c"
done

if [ -n "$SNAPSHOTS" ]; then
    step "Search indexes from $SNAPSHOTS"
    uv run ttdr corpus restore "$SNAPSHOTS"/*.snapshot
else
    step "Search indexes (embeddings and BM25; resumable)"
    uv run ttdr corpus index financebench --recipe "$RECIPE"
    uv run ttdr corpus index unidoc --recipe "$RECIPE"
    uv run ttdr corpus index browsecomp-plus --recipe recipes/browsecomp-plus.toml
fi

step "Check"
uv run ttdr health --recipe "$RECIPE"
cat <<EOF

Ready. Run a suite, for example:
  uv run ttdr eval run deepagents -s financebench-dev --recipe $RECIPE --concurrency 4
Grading and the rest: docs/EVALS.md
EOF
