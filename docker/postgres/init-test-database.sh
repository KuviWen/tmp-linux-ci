#!/bin/sh
set -eu

: "${POSTGRES_TEST_DB:?POSTGRES_TEST_DB is required}"
: "${POSTGRES_TEST_USER:?POSTGRES_TEST_USER is required}"
: "${POSTGRES_TEST_PASSWORD:?POSTGRES_TEST_PASSWORD is required}"

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=test_database="$POSTGRES_TEST_DB" \
  --set=test_user="$POSTGRES_TEST_USER" \
  --set=test_password="$POSTGRES_TEST_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION',
  :'test_user',
  :'test_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'test_user')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'test_database', :'test_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'test_database')
\gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'test_database')
\gexec

SELECT format(
  'GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I',
  :'test_database',
  :'test_user'
)
\gexec
SQL

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_TEST_DB" \
  --set=test_user="$POSTGRES_TEST_USER" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO :"test_user";
SQL
