# Showcase data policy

This repository is a code and product demonstration. The original source
registry workbook and generated per-source inventory snapshots are not included.
They remain in the private project repository.

The registry importer accepts a workbook explicitly from the operator. The
expected workbook contains the six sheets and validated columns consumed by
`ingestion.workbook_registry`; it is not bundled with this repository. Put an
authorized local copy under `local-data/`; Compose mounts the project directory
into the web container. That directory is ignored by Git and excluded from the
Docker build context. Alternatively, mount the file separately and pass its path.

Dry-run first:

```powershell
docker compose -p geoklimat-demo exec -T web python manage.py import_registry /app/local-data/registry.xlsx
```

Apply only after reviewing the dry-run:

```powershell
docker compose -p geoklimat-demo exec -T web python manage.py import_registry /app/local-data/registry.xlsx --apply
```

Tests create a small synthetic workbook in a temporary directory. It uses no
production source list or user-provided registry content.
