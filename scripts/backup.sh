#!/bin/sh
# Run inside the PostgreSQL container. No credentials appear in command output.
set -eu
umask 077
mkdir -p /backups
target="/backups/geoklimat-$(date -u +%Y%m%dT%H%M%SZ).dump"
test ! -e "$target"
pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format custom --file "$target.tmp"
mv "$target.tmp" "$target"
printf '%s\n' "$target"
