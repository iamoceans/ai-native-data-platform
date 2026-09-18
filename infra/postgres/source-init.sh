#!/bin/bash
# Runs once on first initialization of the source PostgreSQL (docker-entrypoint-initdb.d).
# Creates the read-only account used by the PostgreSQL provider (spec 9.2) and
# grants it SELECT only. Write access is intentionally not granted: acceptance
# item A05 relies on the database rejecting writes even if the AST layer is
# bypassed.
set -euo pipefail

: "${SOURCE_DB_PASSWORD:?SOURCE_DB_PASSWORD must be set for source-init}"
: "${SOURCE_DB_USER:=source_reader}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${SOURCE_DB_USER}') THEN
    CREATE ROLE ${SOURCE_DB_USER} LOGIN PASSWORD '${SOURCE_DB_PASSWORD}';
  ELSE
    ALTER ROLE ${SOURCE_DB_USER} PASSWORD '${SOURCE_DB_PASSWORD}';
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${SOURCE_DB_USER};
GRANT USAGE ON SCHEMA public TO ${SOURCE_DB_USER};
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ${SOURCE_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
  GRANT SELECT ON TABLES TO ${SOURCE_DB_USER};
EOSQL

echo "source-init: read-only role ${SOURCE_DB_USER} ready"
