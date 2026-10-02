#!/usr/bin/env bash
# Capture a resource baseline for the local Lattix xFrontier stack.
# Writes a timestamped snapshot to docs/perf/baselines/. Read-only.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out_dir="$repo_root/docs/perf/baselines"
mkdir -p "$out_dir"
out_file="$out_dir/baseline-$(date +%Y%m%d-%H%M%S).txt"

{
  echo "# Lattix xFrontier resource baseline — $(date -Iseconds)"
  echo
  echo "## Host"
  if command -v free >/dev/null 2>&1; then
    free -h
  else
    vm_stat 2>/dev/null || true
  fi
  echo
  echo "## Stack-related processes (node / python / uvicorn / next)"
  ps -eo pid,rss,command | grep -E 'node|python|uvicorn|next' | grep -v grep \
    | awk '{printf "%8s %8.0f MB  ", $1, $2/1024; for (i=3; i<=NF && i<12; i++) printf "%s ", $i; print ""}' \
    | sort -k2 -rn || true
  echo
  echo "## Containers (docker stats, one sample)"
  docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' 2>/dev/null \
    || echo "(docker not running or no containers)"
} | tee "$out_file"

echo "baseline written: $out_file"
