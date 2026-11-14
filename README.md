# GeoKlimat

GeoKlimat is an early-stage public-interest prototype for public-administration
staff, GIS specialists and environmental teams in Poland and the EU. It is
designed to bring environmental notices, regulatory changes, spatial-data
services and selected measurement or satellite catalogue metadata into one
searchable, source-linked workspace.

## Why this project matters

Environmental information is spread across institutional newsrooms, legal
portals, open-data catalogues, spatial services and measurement platforms.
GeoKlimat aims to make changes easier to discover and review while keeping the
origin of each item visible. It is a discovery and monitoring layer: it does
not replace official source systems, legal interpretation or professional
decision-making.

## Intended users

- Public-administration teams tracking relevant notices and regulatory changes.
- GIS specialists following spatial services and data-catalogue updates.
- Environmental professionals reviewing information across institutional
  channels.

## What it demonstrates

- Django application with PostgreSQL-backed source and data registries.
- Bounded feed and public API collection with source-level health and approval
  controls.
- Optional local AI assistance for editorial drafts, with human review before
  publication.
- Search and review of regulatory, institutional and environmental updates.
- GIS discovery with geometry-aware queries and paginated results.
- STAC catalogue and scene metadata workflows, with explicit storage limits and
  retention controls.
- Docker Compose development environment and an automated test suite.

The project is a development prototype. Available records and provider coverage
depend on the data supplied locally and the source permissions configured by an
operator. A catalogue entry alone does not mean that its underlying data has
been collected or accepted.

## Run locally

Install Docker Desktop with Compose v2, then follow the [local installation and
recovery guide](docs/ODTWORZENIE-Z-GITHUB.md). The guide keeps local database
volumes separate and creates an administrator interactively.

The source registry workbook is intentionally excluded from this showcase copy.
See [how local registry data is supplied](docs/SHOWCASE-DATA.md). Synthetic
test data is generated only in temporary test directories.

## Technical notes

- [Product brief](docs/EPIC-0.md)
- [Source catalogue](sources/sources.yaml): monitored sources, adapters and
  schedules kept as configuration.
- [SOPO access hold](docs/SOPO-ACCESS-HOLD.md): an example of a provider whose
  terms keep automated collection switched off.
- [Showcase data policy](docs/SHOWCASE-DATA.md)

## Snapshot policy

This repository is refreshed once a month with one snapshot commit
(`Monthly snapshot YYYY-MM`) taken from the private development repository.
Each snapshot leaves out the operator's source registry workbook, project
notes, internal plans and history, and is scanned for credentials and personal
data before it is published. Development happens elsewhere, so pull requests
here are not merged into the next snapshot.

## Checks

Inside the Compose web container:

```powershell
python manage.py check
python manage.py test
ruff check .
ruff format --check .
```

Provider integrations are bounded and require explicit review. Do not enable a
collector merely to populate a demo; use mocked responses for tests and follow
the provider's published access and reuse terms.
