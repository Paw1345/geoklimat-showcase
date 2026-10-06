# Source policy and atomic import guard

The collector checks a source's current configuration and approval before
requesting data. It performs network I/O without holding a database row lock.
Before saving the response, it locks and rechecks the source inside the write
transaction. If the profile or approval changed, the batch is rejected and
none of its parsed entries are saved.

The checked profile includes the adapter, URL, nested configuration, name,
organization, language, scope, and reuse policy. Operational fields such as
poll interval and error counters can change independently. The scheduler uses
the latest interval and failure count after the batch.

This protects the save boundary while keeping provider requests outside a long
database transaction. A request already sent cannot be recalled. A temporary
revocation followed by restoration of an identical profile during one request
is not tracked as a separate configuration generation.

`ingest --dry-run` fetches and validates a bounded response without storing
entries or creating approval. `--force` may bypass the scheduled time, but it
does not bypass activation or approval requirements. Data-channel collectors
use their own policy checks.

Relevant tests are in `ingestion/test_source_policy.py`; they use mocked
responses and exercise profile changes during fetch, `304` responses, and the
write-lock boundary. Tests do not call external providers.
