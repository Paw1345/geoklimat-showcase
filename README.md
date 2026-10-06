# GeoKlimat

GeoKlimat is a prototype for monitoring environmental and climate information
used by public administration, GIS specialists and environmental teams. It
brings news, regulatory updates, data catalogues, spatial services and selected
measurement or satellite metadata into one searchable application.

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

- [Spatial geometry and query behavior](docs/SPATIAL-GEOMETRY.md)
- [Spatial result paging](docs/SPATIAL-RESULT-PAGES.md)
- [Data storage and retention](docs/DATA-STORAGE.md)
- [STAC collection and coverage workflows](docs/STAC-COVERAGE.md)
- [Showcase data policy](docs/SHOWCASE-DATA.md)

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
