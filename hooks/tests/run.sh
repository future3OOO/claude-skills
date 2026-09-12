#!/usr/bin/env bash
# Integrated workflow verification. Tests are dealt across worker processes rather
# than run one module at a time: each case builds its own scratch repository and
# ledger, each job gets its own CLAUDE_HOME, and the Repo Context Forge wrapper
# serialises Repo Context Forge intakes, whose producers would otherwise tear
# GitNexus's global registry. A direct gitnexus call stays outside that lock.
#
# With no arguments the whole suite runs. Arguments select files or unittest ids;
# anything unittest can load is dealt case by case too, so a targeted selection of
# the slow modules parallelises instead of pinning one module per worker.
# HOOKS_TEST_WORKERS=1 restores single-process execution.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
export PYTHONDONTWRITEBYTECODE=1
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
# Cores are the wrong bound on their own. A shard running a real Repo Context
# Forge intake peaked at 1635MB here, and a WSL host caps memory (24GB) while
# leaving every core visible (24), so the core count alone asked for ~39GB and
# took the VM down with it. Take whichever bound is lower, and leave
# HOOKS_TEST_WORKERS as the override for a caller who knows the shape of its run.
SHARD_PEAK_MB=1700
workers="${HOOKS_TEST_WORKERS:-}"
if [ -z "$workers" ]; then
  workers="$(nproc)"
  by_memory=$(( $(awk '/^MemAvailable:/ {print $2}' /proc/meminfo) / 1024 / SHARD_PEAK_MB ))
  [ "$by_memory" -lt "$workers" ] && workers="$by_memory"
  [ "$workers" -lt 1 ] && workers=1
fi

run_job() {
  local job="$1"
  local home
  # Jobs run at the same time, so the runner owns each job's estate state.
  home="$(mktemp -d "$scratch/home-XXXXXX")"
  export CLAUDE_HOME="$home"
  case "$job" in
    *.sh) bash "$job" ;;
    *.py) python3 -u "$job" ;;
    *) python3 -u -m unittest ${job} ;;   # one shard: one or more unittest ids
  esac
}
export -f run_job
export scratch

cd "$ROOT"
# Command substitution, not process substitution: set -e sees the dealer's status
# here, and a dealer that failed must stop the run rather than have it proceed on
# whatever the dealer managed to print.
dealt="$(python3 "$ROOT/hooks/tests/deal.py" "$workers" "$@")"
mapfile -t jobs <<< "$dealt"
printf '%s\0' "${jobs[@]}" | xargs -0 -P "$workers" -n 1 bash -c 'run_job "$0"'
