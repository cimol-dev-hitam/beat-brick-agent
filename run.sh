#!/bin/sh
# Beat Brick — one-command runner.
# Usage: ./run.sh [tickets.jsonl] [out.jsonl]
# Defaults: sample-tickets.jsonl next to this script -> out.jsonl
set -e
cd "$(dirname "$0")"
if [ -n "$1" ]; then IN="$1"; elif [ -f sample-tickets.jsonl ]; then IN="sample-tickets.jsonl"; else IN="/home/hatch/workspace/housebets/sample-tickets.jsonl"; fi
OUT="${2:-out.jsonl}"
python3 agent.py "$IN" "$OUT"
