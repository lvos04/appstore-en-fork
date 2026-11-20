#!/usr/bin/env bash
set -euo pipefail
: "${REGISTRY_USER:?}" "${REGISTRY_TOKEN:?}"
registry=git.medialeon.nl
image="$registry/leon/appstore-en-fork"
versie=$(cat VERSION)
bron=deploy/manifest/appstore-en-fork.manifest.yaml
doel=release/appstore-en-fork.manifest.yaml
installatie=$(python -c 'import sys,yaml; print(yaml.safe_load(open(sys.argv[1]))["image"]["repository"])' "$bron")
mkdir -p release

# Docker vraagt de jobreferentie op via stdin/stdout. Het configuratiebestand bevat alleen de helpernaam.
export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d "$RUNNER_TEMP/docker.XXXXXXXX")
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
export PATH="$DOCKER_CONFIG:$PATH"
printf '{"credHelpers":{"%s":"forgejo"}}\n' "$registry" > "$DOCKER_CONFIG/config.json"
cat > "$DOCKER_CONFIG/docker-credential-forgejo" <<'HELPER'
#!/bin/sh
[ "$1" = get ] && [ "$(cat)" = git.medialeon.nl ] || exit 1
exec jq -cn '{Username: env.REGISTRY_USER, Secret: env.REGISTRY_TOKEN}'
HELPER
chmod 700 "$DOCKER_CONFIG/docker-credential-forgejo"
if docker buildx imagetools inspect "$image:$versie" --format '{{json .Manifest}}' \
        > "$RUNNER_TEMP/versie.json" 2> "$RUNNER_TEMP/registerfout"; then
    digest=$(jq -er '.digest | select(test("^sha256:[a-f0-9]{64}$"))' "$RUNNER_TEMP/versie.json")
else
    # Een storing of geweigerde toegang geeft geen toestemming om een versietag te overschrijven.
    if ! grep -Eiq 'manifest unknown|name unknown|not found|404' "$RUNNER_TEMP/registerfout"; then
        cat "$RUNNER_TEMP/registerfout" >&2
        exit 1
    fi
    bouwer=$(docker buildx create --driver docker-container)
    trap 'docker buildx rm "$bouwer" >/dev/null; rm -rf "$DOCKER_CONFIG"' EXIT
    docker buildx build --builder "$bouwer" --file deploy/allinone/Dockerfile \
        --build-arg "VERSION=$versie" --label "org.opencontainers.image.revision=$GITHUB_SHA" --tag "$image:$versie" \
        --push --provenance=mode=max --sbom=true --metadata-file "$RUNNER_TEMP/bouw.json" .
    digest=$(jq -er '."containerimage.digest" | select(test("^sha256:[a-f0-9]{64}$"))' "$RUNNER_TEMP/bouw.json")
fi
bouwtijd=$(docker buildx imagetools inspect "$image@$digest" --format '{{json .Image}}' |
    jq -er --arg sha "$GITHUB_SHA" 'select(.config.Labels["org.opencontainers.image.revision"] == $sha) | .created')
docker buildx imagetools inspect "$image@$digest" --format '{{json .SBOM}}' |
    jq -e '.SPDX | select(.spdxVersion | startswith("SPDX-"))' > release/sbom.spdx.json
docker buildx imagetools inspect "$image@$digest" --format '{{json .Provenance}}' |
    jq -e '.SLSA | .buildType // .buildDefinition.buildType' >/dev/null
jq -n --arg repo "$image" --arg versie "$versie" --arg digest "$digest" --arg sha "$GITHUB_SHA" --arg tijd "$bouwtijd" \
  '{images:[{repository:$repo,tag:$versie,digest:$digest}],commit:$sha,built_at:$tijd}' > release/digests.json

# Het bouwbewijs hoort bij Forgejo. Alleen een in het installatieregister bevestigde digest mag naar de NAS.
kandidaat="$RUNNER_TEMP/kandidaat.manifest.yaml"
python tools/release_manifest.py "$bron" "$kandidaat" --version "$versie" --digest "$digest" --commit "$GITHUB_SHA"
jl-manifest validate --strict "$kandidaat"
if installatie_digest=$(docker buildx imagetools inspect "$installatie:$versie" --format '{{json .Manifest}}' |
                       jq -er '.digest') && [[ "$installatie_digest" == "$digest" ]]; then
    cp "$kandidaat" "$doel"
    status="De digest van het NAS-manifest is ook in het installatieregister bevestigd."
else
    cp "$bron" "$doel"
    status="De NAS-installatie wacht op digestbevestiging op GHCR. Het bijgevoegde bronmanifest houdt de installatie geblokkeerd."
fi
jl-manifest validate --strict "$doel"
printf 'Image %s:%s@%s. %s\n\nAlleen met simulators getest. Zie CHANGELOG.md.\n' \
    "$image" "$versie" "$digest" "$status" > "$RUNNER_TEMP/releasenotities"
