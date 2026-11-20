#!/usr/bin/env bash
# De runner gebruikt het hostnetwerk. Docker kiest per testjob een vrije loopbackpoort.
set -euo pipefail
idbestand="$RUNNER_TEMP/appstore-postgres.id"
if [[ "${1:-}" == stop ]]; then
    if [[ -s "$idbestand" ]]; then
        docker rm -fv "$(cat "$idbestand")" >/dev/null
    fi
    exit 0
fi
image=mirror.gcr.io/library/postgres:16.15-bookworm@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9
loopback=$(getent ahostsv4 localhost | awk 'NR == 1 {print $1}')
test -n "$loopback"
id=$(docker run --detach --rm --publish "$loopback::5432" --env POSTGRES_PASSWORD=ci-alleen-voor-tests \
    --health-cmd 'pg_isready -h localhost -U postgres' --health-interval 2s --health-timeout 5s --health-retries 30 "$image")
echo "$id" > "$idbestand"
for _ in {1..60}; do
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' "$id")" == healthy ]]; then break; fi
    sleep 1
done
test "$(docker inspect --format '{{.State.Health.Status}}' "$id")" = healthy
binding=$(docker port "$id" 5432/tcp)
echo "APPSTORE_TEST_DATABASE_URL=postgresql+psycopg://postgres:ci-alleen-voor-tests@localhost:${binding##*:}/postgres" >> "$GITHUB_ENV"
