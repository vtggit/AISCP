#!/usr/bin/env bash
# Run the AIERP backend test suite against a containerized PostgreSQL.
#
# Usage:
#   cd backend
#   ./run_tests.sh              # run all tests
#   ./run_tests.sh -k test_auth # pass extra pytest args
#
# This script:
#   1. Starts an ephemeral postgres:15 container named aierp-test-db
#      (no persistent volume), publishing host port 5433:5432
#   2. Waits for it to be ready (pg_isready, up to 30 seconds)
#   3. Runs pytest with DB_PORT=5433 (test container)
#   4. Tears down the container
#
# Requires: docker (no sudo apt install postgresql)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_DB_PORT=5433

# ---------------------------------------------------------------------------
# Default environment variables for the test database
# (override by setting these in your shell or .env file)
# ---------------------------------------------------------------------------
export DB_NAME="${DB_NAME:-aierp}"
export DB_USER="${DB_USER:-aierp}"
export DB_PASSWORD="${DB_PASSWORD:-change-me-in-production}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# shellcheck disable=SC2317,SC2329  # cleanup + its body run via the 'trap cleanup EXIT' below
cleanup() {
    echo ""
    echo "==> Stopping test database container..."
    docker rm -f aierp-test-db >/dev/null 2>&1 || true
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

if ! command -v docker &>/dev/null; then
    echo "ERROR: docker is not installed or not in PATH"
    exit 1
fi

# ---------------------------------------------------------------------------
# Start the ephemeral test database
# ---------------------------------------------------------------------------

echo "==> Starting ephemeral test database on port $TEST_DB_PORT..."
# Remove a leftover container of the same name from an interrupted run, if any.
docker rm -f aierp-test-db >/dev/null 2>&1 || true

docker run -d --rm \
    --name aierp-test-db \
    -p "${TEST_DB_PORT}:5432" \
    -e "POSTGRES_DB=${DB_NAME}" \
    -e "POSTGRES_USER=${DB_USER}" \
    -e "POSTGRES_PASSWORD=${DB_PASSWORD}" \
    postgres:15 >/dev/null

echo "==> Waiting for PostgreSQL to be ready..."
# Wait up to 30 seconds for the DB to accept connections
for i in $(seq 1 30); do
    if docker exec aierp-test-db pg_isready -U "${DB_USER}" -d "${DB_NAME}" &>/dev/null; then
        echo "==> PostgreSQL is ready."
        break
    fi
    sleep 1
    if [ "$i" -eq 30 ]; then
        echo "ERROR: PostgreSQL did not become ready in time"
        exit 1
    fi
done

# ---------------------------------------------------------------------------
# Run pytest
# ---------------------------------------------------------------------------

echo "==> Running backend tests..."

# Export test DB and auth config so conftest.py picks them up at import time
export DB_HOST="localhost"
export DB_PORT="$TEST_DB_PORT"
export AUTH_MODE="development"
export AUTH_DEV_TOKEN="dev-secret-token"

cd "$SCRIPT_DIR"
python3 -m pytest tests/ -v --tb=short "$@"
EXIT_CODE=$?

# ---------------------------------------------------------------------------
# Exit
# ---------------------------------------------------------------------------

if [ "$EXIT_CODE" -eq 0 ]; then
    echo ""
    echo "==> All tests passed."
else
    echo ""
    echo "==> Some tests failed (exit code $EXIT_CODE)."
fi

exit "$EXIT_CODE"
