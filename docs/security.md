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
- Login rate limiting: fixed failure windows per hashed (username, client IP),
  stored in PostgreSQL and shared across API processes. Check/verify/update is
  serialized by per-key transaction locks; at most 10,000 live keys are allocated,
  expired keys are reclaimed and saturation denies new keys until expiry.
  Successful verification resets the count. The backend image and native Uvicorn
  launches must use `--no-proxy-headers`, so forwarded headers cannot change the
  limiter key. Direct peer IPs are used; clients behind one proxy share that peer.
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

Account settings (2026-10-16): PATCH `/auth/me` derives its target solely from
the authenticated user, requires CSRF and a matching persisted settings version,
and forbids identity, role, capability or password fields. Display name is bounded
plain text rendered by React; language and IANA timezone are validated. Audits
record changed field names. Account settings persist on the server; browser storage
contains only the guest locale and cross-tab notification markers. These markers
authorize nothing. Other workspace tabs clear caches/reload on authentication
changes. Timezone preferences affect display only, not query permissions or period
interpretation. Real PostgreSQL and browser tests verify isolation and stale saves.

The 2026-10-16 live protocol tests supersede the older pending note for PostgreSQL
TLS negotiation and MySQL/Doris greeting stalls. They do not establish a deployed
Doris server acceptance. Spark cancellation now uses a separate HS2 connection,
verified against real Spark 3.5.3; verified TLS and real PLAIN identity rejection/
acceptance now pass on the standalone custom-authenticator fixture. Source
read-only authorization remains unverified. The dedicated seven-write verifier
rejects that unprotected fixture and only counts recognizable authorization
denials, unchanged reads and successful cleanup as acceptance of a real policy.

Self-service password rotation (2026-10-16) requires current credentials and CSRF.
New passwords are 12..1024 characters, represented as SecretStr in the closed DTO;
validation responses omit raw inputs. Verification failures are audited without
password values and rate-limited in an account-specific bucket. Rotation hashes
and revokes all sessions in one transaction; login verification/session issuance
uses the same user row lock so a concurrent old-password login cannot survive
revocation. Other accounts are unaffected. Cookies are deleted on both password
change and logout; cross-tab auth notifications trigger the existing cache reset.
The production routes now use PostgreSQL auth_rate_limits (migration
f6a12c83d095), not the standalone in-memory test helper. Fixed failure windows
survive new processes; per-key transaction locks serialize check/verify/failure
updates, and a capacity lock only allocates new keys (at most 10,000 live keys).
Expired keys are reclaimed on allocation. Limiter locks precede user locks in both
login and password change; successful verification resets failures. Denied audit
commits preserve failed attempts. Database failures never fall back to local state.

Saved query templates remain private to their owner. Frontend loading only sets
editor values, without execution; the Query Gateway remains authoritative for
SQL/grant validation when the user explicitly runs a loaded template.

Spark scalar binding (2026-10-16) uses SQLGlot AST literals and refuses missing,
extra, anonymous, nested or nonfinite bindings. It requires an exact Spark AST
parse round trip after serialization; no string interpolation or Impyla formatting
is used. The source SQL remains gateway-validated and worker-revalidated. Both
statement kinds/registered datasets and certificate/hostname verification remain
binding; PLAIN without TLS is rejected by config and provider checks.

All query/analysis/session SSE loops now check persisted authentication-session
expiry/revocation and active user state before publishing another poll. Query and
analysis streams recheck dataset grants before replay; a session stream suppresses
revoked-analysis events and emits its revocation notification. A model or account
default never sets dates or verified completeness. Report reruns are CSRF protected,
owner/capability/grant checked and create a new child; immutable old reports remain.

DataHub external failures (2026-10-16): neither the adapter nor token helper treats
HTTP errors/redirects, malformed envelopes or missing operation fields as a valid
empty catalog or authenticated success. Adapter errors omit remote diagnostic
text, which may contain echoed inputs. The token helper's --verify-auth mode
writes nothing and requires explicit 401/403 denials for anonymous/random-invalid
requests plus a valid-token GraphQL result. Outages do not establish authentication.
Token-file/environment input avoids putting secrets in process arguments; the
legacy option remains compatible. Storage uses a private temporary file, fsync
and atomic replacement, preserving the old secret if replacement fails. This
local contract coverage does not establish actual auth-enabled GMS deployment.

Memory recall (2026-10-16) checks team/private scope, active reader and complete
source provenance in SQL before its 1000-candidate limit. Correlated EXISTS
requires both current discover and query grants for every dependency, including
multi-query/multi-dataset analyses; partial/expired grants fail closed and grants
may combine across roles. Unauthorized memories never enter semantic ranking.
List and aggregate counts use the same predicate. At most eight statements are
injected; widening candidates does not widen prompt output or authorize any
analysis action. Missing identity/roles and disabled owners fail closed. Real
PostgreSQL tests cover starvation, expirations, split grants and team/private scope.

Concurrent reuse accounting (2026-10-17) remains inside the fenced analysis
transaction. Atomic increments do not grant visibility or widen advisory context;
read-time source/team checks still choose eligible IDs. Multi-row locks use a
stable ID order and last-used time is monotonic. A failed/rolled-back transaction
cannot leave a committed reuse increment behind.
