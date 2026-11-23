#!/usr/bin/env bash
# The merge gate's e2e pass: run the specs in app/e2e/ on the real stack and add their
# junit to the gate's junit, so the contract gate checks an e2e proof itself.
#
# The engine's build gate proves an [e2e-proof] slice the same way:
#   - a freshly migrated database, seeded from app/e2e/seed.sql when that file exists;
#   - the backend, served from this checkout;
#   - a proxy that serves app/ and forwards /api/ as production's nginx does;
#   - the specs, given BASE_URL and E2E_TOKEN (the development sign-in token).
#
# An infrastructure failure exits 1, named, before any spec verdict. A spec's own verdict
# is left to the contract gate, as in the mocked pass.
#
# Environment:
#   DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD  the database the backend migrates and uses
#   AUTH_MODE=development, AUTH_DEV_TOKEN        the development sign-in (the specs' token)
#   JUNIT_OUT     the gate's junit: the e2e suites are appended (it is created when absent)
#   PG_CONTAINER  the Postgres container that loads app/e2e/seed.sql, when that file exists
#   BACKEND_PORT  default 9000
#   PROXY_PORT    default 8090 (the mocked pass's static server keeps 8080)
#   PW_IMAGE      default: the Playwright image the mocked pass uses
#   LOG_DIR       where the stack's logs go (default: $RUNNER_TEMP, else /tmp)
set -eu

FRONTEND=app
E2E_DIR="$FRONTEND/e2e"
HEALTH_PATH=/api/health
BACKEND_PORT="${BACKEND_PORT:-9000}"
PROXY_PORT="${PROXY_PORT:-8090}"
PW_IMAGE="${PW_IMAGE:-mcr.microsoft.com/playwright:v1.57.0-noble}"
LOG_DIR="${LOG_DIR:-${RUNNER_TEMP:-/tmp}}"
: "${JUNIT_OUT:?JUNIT_OUT must name the gate junit}"
: "${AUTH_DEV_TOKEN:?AUTH_DEV_TOKEN must hold the development sign-in token}"
: "${DB_USER:?DB_USER must be set}" "${DB_NAME:?DB_NAME must be set}"
BASE_URL="http://127.0.0.1:$PROXY_PORT"

# fail MESSAGE [LOG...]: name the failure for the job summary, show each log's tail, stop.
fail() {
  echo "::error::$1"
  shift
  local log
  for log in "$@"; do
    if [ -f "$log" ]; then
      echo "--- $log (last 60 lines)"
      tail -n 60 "$log"
    fi
  done
  exit 1
}

# status URL: the HTTP status the URL answers with, or "none" when nothing answers.
status() {
  python - "$1" <<'PY'
import sys
import urllib.error
import urllib.request

try:
    print(urllib.request.urlopen(sys.argv[1], timeout=2).status)
except urllib.error.HTTPError as exc:
    print(exc.code)
except Exception:
    print("none")
PY
}

PIDS=()
cleanup() {
  local pid
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT

[ -f "$E2E_DIR/playwright.config.js" ] ||
  fail "$E2E_DIR/playwright.config.js is missing: the engine's gate writes it with the first e2e spec. No e2e spec ran."
mkdir -p "$LOG_DIR"

echo "e2e: migrating the database"
(cd backend && python -m alembic upgrade head) >"$LOG_DIR/e2e-migrate.log" 2>&1 ||
  fail "the migrations failed: an infrastructure failure, no e2e spec ran" "$LOG_DIR/e2e-migrate.log"

if [ -f "$E2E_DIR/seed.sql" ]; then
  echo "e2e: loading $E2E_DIR/seed.sql"
  : "${PG_CONTAINER:?PG_CONTAINER must name the Postgres container that loads seed.sql}"
  docker exec -i "$PG_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q \
    <"$E2E_DIR/seed.sql" >"$LOG_DIR/e2e-seed.log" 2>&1 ||
    fail "$E2E_DIR/seed.sql did not load: no e2e spec ran" "$LOG_DIR/e2e-seed.log"
fi

echo "e2e: starting the backend on port $BACKEND_PORT"
(cd backend && exec python -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT") \
  >"$LOG_DIR/e2e-backend.log" 2>&1 &
BACKEND_PID=$!
PIDS+=("$BACKEND_PID")
# Ready on a 2xx. A 4xx will not change (the route is wrong or closed) and a backend that
# exited will not come back, so both fail at once; a 5xx or no answer keeps waiting.
code=none
deadline=$((SECONDS + 90))
while [ "$SECONDS" -lt "$deadline" ]; do
  code="$(status "http://127.0.0.1:$BACKEND_PORT$HEALTH_PATH")"
  case "$code" in
    2??) break ;;
    4??) fail "the backend answered $HEALTH_PATH with HTTP $code; it must be an open route that answers 2xx. No e2e spec ran." "$LOG_DIR/e2e-backend.log" ;;
  esac
  kill -0 "$BACKEND_PID" 2>/dev/null ||
    fail "the backend exited before it answered $HEALTH_PATH: no e2e spec ran" "$LOG_DIR/e2e-backend.log"
  sleep 1
done
case "$code" in
  2??) ;;
  *) fail "the backend never answered $HEALTH_PATH with a 2xx in 90 s (last: $code): no e2e spec ran" "$LOG_DIR/e2e-backend.log" ;;
esac

echo "e2e: serving $FRONTEND/ on port $PROXY_PORT, with /api/ forwarded to the backend"
python scripts/ca_gate/e2e_stack.py proxy --root "$FRONTEND" --port "$PROXY_PORT" \
  --backend "http://127.0.0.1:$BACKEND_PORT" >"$LOG_DIR/e2e-proxy.log" 2>&1 &
PIDS+=("$!")
deadline=$((SECONDS + 15))
until [ "$(status "$BASE_URL/")" = 200 ]; do
  [ "$SECONDS" -lt "$deadline" ] ||
    fail "the e2e proxy never became ready at $BASE_URL/: an infrastructure failure, no e2e spec ran" "$LOG_DIR/e2e-proxy.log"
  sleep 0.2
done

echo "e2e: running $E2E_DIR/ in $PW_IMAGE against $BASE_URL"
rm -f "$E2E_DIR/report.xml"
rc=0
# The single-quoted command expands $PWD inside the container, on purpose.
# shellcheck disable=SC2016
docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e BASE_URL="$BASE_URL" -e E2E_TOKEN="$AUTH_DEV_TOKEN" \
  -v "$PWD/$FRONTEND":/app -w /app/e2e "$PW_IMAGE" bash -c \
  'npm i --prefix "$PWD" @playwright/test@1.57.0 --no-save --no-audit --no-fund --silent && npx playwright test --config playwright.config.js --output=test-results' ||
  rc=$?
[ -f "$E2E_DIR/report.xml" ] ||
  fail "playwright produced no e2e junit (rc=$rc): an npx or container failure, not a spec verdict"
if grep -E 'net::ERR_CONNECTION_|net::ERR_EMPTY_RESPONSE|ECONNREFUSED' "$E2E_DIR/report.xml" |
  grep -q "127.0.0.1:$PROXY_PORT"; then
  fail "e2e spec(s) could not reach the app at $BASE_URL: an infrastructure failure, not an assertion" "$LOG_DIR/e2e-proxy.log"
fi
echo "e2e: playwright exited rc=$rc; the contract gate enforces the proof verdicts"
python scripts/ca_gate/e2e_stack.py merge-junit --into "$JUNIT_OUT" \
  --extra "$E2E_DIR/report.xml" --file-prefix "$E2E_DIR"
