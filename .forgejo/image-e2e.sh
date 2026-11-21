#!/usr/bin/env bash
# De testclient deelt met de geïsoleerde bouwdaemon de loopback en één tijdelijke datamap.
# Zo toetsen dezelfde tests een echte bind mount en de alleen op loopback gepubliceerde apppoort.
set -euo pipefail
proefbestand="$RUNNER_TEMP/appstore-e2e.proef"
if [[ ${1:-} == ruim ]]; then
    # Afsluitende stap: ook na afbreken de testclient en alle containers van deze proef weg.
    if [[ -s $proefbestand ]]; then
        docker ps -aq --filter "label=nl.medialeon.ci.proef=$(cat "$proefbestand")" | xargs -r docker rm -fv >/dev/null
        docker volume rm "$(cat "$proefbestand")" >/dev/null 2>&1 || true
    fi
    exit 0
fi
: "${APPSTORE_E2E_IMAGE:?}"
volume=$(docker volume create)
echo "$volume" > "$proefbestand"
testclient="appstore-proef-${volume:0:16}"
opruimen() {
    docker rm -fv "$testclient" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null
    docker image rm "$APPSTORE_E2E_IMAGE" >/dev/null 2>&1 || true
}
trap opruimen EXIT
docker build -f deploy/allinone/Dockerfile -t "$APPSTORE_E2E_IMAGE" .
datamap=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
cli=$(command -v docker)
tar -c -f - apps/api/tests tests/e2e .forgejo/proefdocker.sh -C "$(dirname "$cli")" docker |
    docker run --rm -i --name "$testclient" --network host \
        --label "nl.medialeon.ci.proef=$volume" -e "CI_PROEF=$volume" \
        --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
        --mount "type=volume,source=$volume,target=$datamap" \
        -e APPSTORE_E2E_IMAGE -e "TMPDIR=$datamap" --workdir /werk \
        --entrypoint /bin/sh "$APPSTORE_E2E_IMAGE" -ec '
            tar -xf -
            install -m 0755 docker /usr/bin/docker
            install -m 0755 .forgejo/proefdocker.sh /usr/local/bin/docker
            python -m pip install --disable-pip-version-check --no-cache-dir pytest==9.2.0
            exec python -m pytest -q tests/e2e/test_allinone_image.py
        '
