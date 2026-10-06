# Repository guidance

- This is the public showcase copy. The user-provided source registry workbook,
  per-source inventory snapshots, local databases, backups, and credentials are
  intentionally excluded. Keep operator-supplied files under ignored `local-data/`.
- Python and database tooling run through Docker Compose. Do not install project
  dependencies globally on the host.
- Provider integrations must stay bounded, use approved public endpoints, and
  respect published access and reuse terms. Tests should use synthetic or mocked
  data; do not contact providers as part of tests.
- Source activation and persistent imports require explicit operator review.
- Preserve existing data and cursors. Do not reset or force-stop the shared
  worker or database as part of routine development.
- Run `python manage.py check` and relevant tests inside the Compose web container.
- Review `git status` before edits and `git diff` before committing. Never commit
  `.env`, private workbooks, local-data, backups, or credentials.
