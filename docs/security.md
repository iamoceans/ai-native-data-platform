# Security model and known limits (M1)

## Identity and sessions

- Passwords: Argon2id (`argon2-cffi`), hashes never logged.
- Sessions: 32-byte random tokens; the database stores only SHA-256 hashes
  (`auth_sessions.token_hash`). Cookie is `HttpOnly`, `SameSite=Lax`, `Secure` when
  `AIND_COOKIE_SECURE=true`. TTL 8 hours; logout revokes the row.
- CSRF: a per-session random token; the server stores only its hash. Unsafe methods
  require the `X-CSRF-Token` header and an allowed `Origin` when the browser sends
  one. The token is also mirrored into a readable cookie (`ainative_csrf`) so the SPA
  can recover it after a page reload - a double-submit pattern; the server still
  validates the header against the session hash.
- Login rate limiting: attempts per (username, client IP) in a moving window. This
  limiter is in-process; with more than one API replica a shared store is required
  (noted as a production item). Storage is bounded to 10,000 keys with expired
  buckets pruned; saturation denies new keys until expiry. The backend image
  starts Uvicorn with `--no-proxy-headers`, so arbitrary forwarded headers cannot
  change the limiter key. Native Uvicorn launches must use the same flag. The
  direct peer IP is used; clients behind one reverse proxy share that peer.
- Error uniformity: unknown user, wrong password and inactive account return the
  same 401 message; failures are audited with `outcome=denied`. Login denial
  and `outcome=rate_limited` audits commit before raising the HTTP error, so
  the request dependency's rollback cannot discard them.

## Authorization

- Product capabilities come from roles (`viewer`, `analyst`, `admin`).
- Data access is **separate and default-deny**: per-role grants
  (`permissions(role_id, dataset_id, action)` with `discover`/`query`). Even
  administrators need an explicit grant to read a dataset - managing resources does
  not imply data access.
- Granting `query` automatically ensures `discover` exists.
- Grants are checked at search, submission, execution start, result download and
  SSE replay. Revoking a grant bumps `policy_state.revision` and cancels associated
  active queries in the same transaction.
- Cross-user private resources return `404`, not `403`.
- Query results and query SSE require both current `query` and `discover` grants.
- Datasource connection configuration and `secret_ref` are returned only to
  callers with `admin.manage`; other users receive the selection/status fields.
- Business-memory listing, status counts and curation responses require current
  `query` and `discover` grants on all registered source-analysis dependencies;
  missing provenance fails closed. Counts include only visible statements.
- Query resolution uses only the authorized active catalog. Hidden and unknown
  tables return the same generic refusal before column validation or source
  schema introspection; ambiguity details contain authorized names only.

## SQL safety (defense in depth)

1. **Request**: single statement, SQL size <= 64 KiB, named parameters only.
   `AIND_REQUEST_BODY_MAX_BYTES` caps all HTTP bodies before parsing (default
   4 MiB), including chunked/multipart uploads. Parameters, filters and session
   skill configuration are limited to 100 entries / 64 KiB serialized JSON.
2. **AST**: SQLGlot parse with `RAISE`; `Command` fallbacks and parse warnings are
   rejected; only `SELECT`/`UNION` at the top level; denylist of write/DDL/session/
   locking/`INTO` nodes; executable comments (`/*!`) rejected at the raw-text level;
   recursive CTEs rejected; `FOR UPDATE` rejected.
3. **Functions**: allowlist (aggregates, `case`, `coalesce`, `nullif`, `cast`,
   limited date/string functions). `exp.Anonymous` (any unparsed function) is
   rejected by default - `pg_sleep`, `dblink`, UDFs, `generate_series`, etc.
4. **Joins**: `CROSS JOIN`, `NATURAL JOIN`, joins without `ON`/`USING` and constant
   `ON TRUE` are rejected; relation budget 8, subquery depth 4.
5. **Resolution**: every physical table must be a registered, active dataset on that
   datasource (CTE names are not physical tables); references are rewritten to fully
   qualified names; the executed SQL is re-parsed after serialization.
6. **Schema**: `*` expansion and column validation against the latest snapshot;
   unknown columns are rejected.
7. **Permissions**: `query` + `discover` on every referenced dataset.
8. **Database**: the source account is SELECT-only (no writes, no DDL) and the
   provider session is read-only - verified by integration tests at both layers
   (`SQLSTATE 42501` and `25006`).
9. **Limits**: server `statement_timeout` + `lock_timeout` +
   `idle_in_transaction_session_timeout`, client deadline, concurrency caps,
   row/byte caps, N+1 truncation.
10. **Introspection** uses dedicated provider methods on `information_schema`; system
    tables are never exposed to user SQL.

## Datasource network policy

- Host must match `AIND_SOURCE_HOST_ALLOWLIST` (names, IPs or CIDR entries).
- Link-local, multicast, unspecified addresses and cloud metadata endpoints are
  rejected regardless of the allowlist; Unix-socket / DSN syntax is rejected.
- Registration is admin-only; the response never contains credentials.

## Secrets

- Datasource credentials live only in mounted files
  (`infra/local-secrets/<ref>.json`); the database stores a logical `secret_ref`.
- `SecretResolver` accepts a strict logical-name pattern, resolves inside the
  secrets directory only, and rejects path traversal / arbitrary file reads.
- Control-DB credentials come from `.env` (generated by `make setup-secrets`,
  git-ignored). `.env.example` contains placeholders only.
- Audit details and structured logs redact credential-like keys; SQL is logged as a
  fingerprint (`sql_hash`), never as full text in logs.

## Sensitive data (M1 scope)

- Column-name policy at registration time: base tables whose columns match
  `AIND_SENSITIVE_COLUMN_PATTERNS` (email, phone, ssn, password, token, ...) are not
  registered as queryable datasets (`skipped: SENSITIVE_COLUMNS`).
- Views are registered only when the administrator lists them as confirmed secure
  views; the confirmation basis (and a definition hash when the provider supports
  it) is recorded in the metadata snapshot.
- This is deliberately conservative, not a general column/row-rewriting engine;
  the spec's "secure view" workflow stays the primary mechanism. M3 added the
  DataHub catalog, but DataHub owners/tags remain *context* - authorization is
  still the platform's own `permissions` table, checked at search, submit,
  execute, download, report and SSE time. Lineage nodes are permission-filtered
  before they are returned (unmapped or unauthorized nodes are reported as
  filtered, never leaked).

## What is *not* protected in M1

- Multi-replica API (shared login limiter / SSE fan-out) is out of scope.
- TLS termination and Secure cookies: local HTTP deployment only; loopback binding
  is the compensating control.
- The API process can read source credentials because it performs metadata
  introspection; workers share the same secret mount. A future split (introspection
  service account or worker-only credentials) is a documented production item.
- Sensitive-data detection is name-pattern based; it cannot see semantically
  sensitive values under neutral column names. Registering such tables is an
  administrator decision and is recorded in the audit log.
- M5 model output is accepted only through strict Pydantic schemas. A model can
  select at most three dimensions from the metric definition allowlist; it cannot
  introduce SQL, tools, URLs, files or code. User questions and metadata are
  explicitly treated as untrusted content, and every compiled query still passes
  through the same Query Gateway. The external endpoint, model and API-key file
  remain administrator-only environment configuration.

Provider binding escapes non-placeholder percent text in comments, identifiers
and literals while restoring only AST-recognized bindings. PostgreSQL forwards
the configured `ssl_mode` to libpq; required verification failures are not
downgraded. MySQL/Doris execution sockets have finite read/write timeouts at the
query timeout plus five seconds; other connections use the default timeout plus
five seconds. These controls were regression-tested without live engines on
2026-10-15; deployment acceptance remains pending.
