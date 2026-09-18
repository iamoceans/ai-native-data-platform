#!/bin/bash
# Source MySQL initialization: creates the read-only account used by the MySQL
# provider. Write privileges are intentionally not granted; acceptance A05
# relies on the database rejecting writes even if the AST layer is bypassed.
set -euo pipefail

: "${APP_READER_PASSWORD:?APP_READER_PASSWORD must be set for mysql source init}"
: "${APP_READER_USER:=app_reader}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE must be set}"

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL
CREATE USER IF NOT EXISTS '${APP_READER_USER}'@'%' IDENTIFIED BY '${APP_READER_PASSWORD}';
ALTER USER '${APP_READER_USER}'@'%' IDENTIFIED BY '${APP_READER_PASSWORD}';
GRANT SELECT ON \`${MYSQL_DATABASE}\`.* TO '${APP_READER_USER}'@'%';
FLUSH PRIVILEGES;
EOSQL

echo "mysql source-init: read-only account ${APP_READER_USER} ready"
