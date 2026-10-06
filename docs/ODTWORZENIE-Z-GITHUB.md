# Local setup from the showcase repository

This guide starts a fresh development environment. It does not restore an
existing database or include the private source registry workbook.

## Requirements

- Docker Desktop with the Linux engine and Docker Compose v2.
- Git.
- Free local port8001 and disk space for PostgreSQL and Docker images.

Confirm the tools in PowerShell:

```powershell
git --version
docker version
docker compose version
```

`docker version` must show both Client and Server sections.

## Start an isolated local copy

Use a distinct Compose project name so this setup gets a separate PostgreSQL
volume. Run the commands from the repository directory:

```powershell
docker compose -p geoklimat-demo build web worker
docker compose -p geoklimat-demo up -d --wait db
docker compose -p geoklimat-demo run --rm --no-deps web python manage.py migrate --noinput
docker compose -p geoklimat-demo run --rm --no-deps web python manage.py seed_sources
docker compose -p geoklimat-demo run --rm --no-deps web python manage.py seed_data_channels
docker compose -p geoklimat-demo run --rm web python manage.py createsuperuser
docker compose -p geoklimat-demo up -d web worker
```

The administrator is created interactively; this project ships no default
account. Open <http://localhost:8001/> and sign in with that account.

Stop the demo with `docker compose -p geoklimat-demo down`. This keeps its
database volume. Do not add `-v` unless you intentionally want to delete that
local database.

## Optional registry import

The showcase does not include the operator's six-sheet registry workbook. If
you have an approved copy, place it in the Git-ignored and Docker-ignored
`local-data/registry.xlsx` directory. Then run a dry-run and review its summary:

```powershell
docker compose -p geoklimat-demo run --rm --no-deps web python manage.py import_registry /app/local-data/registry.xlsx
```

Apply only after reviewing the workbook and dry-run:

```powershell
docker compose -p geoklimat-demo run --rm --no-deps web python manage.py import_registry /app/local-data/registry.xlsx --apply
```

Importing registry definitions does not activate source collectors. Each source
requires its own access review, audit and explicit activation decision.

## Checks and logs

```powershell
docker compose -p geoklimat-demo exec -T web python manage.py check
docker compose -p geoklimat-demo exec -T web python manage.py test
docker compose -p geoklimat-demo ps
docker compose -p geoklimat-demo logs --tail=50 web
```

The seeded catalogues can be empty until the operator configures permitted
sources. Do not enable all collectors as a demo shortcut. Review each provider's
terms and use bounded collection settings.
