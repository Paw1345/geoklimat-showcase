#!/bin/sh
set -eu
ollama serve &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
attempt=0
until ollama list >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  test "$attempt" -lt 30
  sleep 1
done
ollama pull qwen3:4b
ollama list
