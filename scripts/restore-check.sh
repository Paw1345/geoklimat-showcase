#!/bin/sh
# Restore exclusively to a new scratch database; never touches the source database.
set -eu
test "$#" -eq 1
test -f "$1"
scratch="restore_check_$(date -u +%Y%m%d%H%M%S)_$$"
createdb --username "$POSTGRES_USER" "$scratch"
trap 'dropdb --username "$POSTGRES_USER" "$scratch"' EXIT
pg_restore --username "$POSTGRES_USER" --dbname "$scratch" --no-owner --exit-on-error "$1"
psql --username "$POSTGRES_USER" --dbname "$scratch" -v ON_ERROR_STOP=1 \
  -c 'SELECT count(*) AS applied_migrations FROM django_migrations;' \
  -c 'SELECT count(*) AS imported_items FROM ingestion_item;'
