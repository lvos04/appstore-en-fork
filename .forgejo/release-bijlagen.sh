#!/usr/bin/env bash
# Publiceer het concept pas nadat alle drie bestanden byte voor byte zijn teruggelezen.
set -euo pipefail
: "${RELEASE_TOKEN:?}"
basis="https://git.medialeon.nl/api/v1/repos/leon/appstore-en-fork"
tag="$GITHUB_REF_NAME"
titel="appstore-en-fork $tag"
antwoord="$RUNNER_TEMP/release.json"
api() {
    curl --silent --show-error -H @<(printf 'Authorization: token %s\n' "$RELEASE_TOKEN") "$@"
}
# Door de tag vooraf op te zoeken kan een handmatige aanroep geen ontbrekende tag aanmaken.
api --fail "$basis/tags/$tag" | jq -e --arg sha "$GITHUB_SHA" '.commit.sha == $sha' >/dev/null
code=$(api -o "$antwoord" -w '%{http_code}' "$basis/releases/tags/$tag")
case "$code" in
    404)
        jq -n --arg tag "$tag" --arg sha "$GITHUB_SHA" --arg titel "$titel" --rawfile tekst "$RUNNER_TEMP/releasenotities" \
            '{tag_name:$tag,target_commitish:$sha,name:$titel,body:$tekst,draft:true}' |
            api --fail -H 'Content-Type: application/json' --data-binary @- "$basis/releases" > "$antwoord"
        ;;
    200) ;;
    *) echo "Forgejo gaf HTTP $code bij het lezen van de release" >&2; exit 1 ;;
esac
id=$(jq -er --arg tag "$tag" --arg titel "$titel" 'select(.tag_name==$tag and .name==$titel) | .id' "$antwoord")
concept=$(jq -r '.draft' "$antwoord")
for bestand in digests.json sbom.spdx.json appstore-en-fork.manifest.yaml; do
    asset=$(jq -r --arg naam "$bestand" '.assets[]? | select(.name==$naam) | .id' "$antwoord")
    url="https://git.medialeon.nl/leon/appstore-en-fork/releases/download/$tag/$bestand"
    if [[ -n "$asset" ]]; then
        api --fail --location "$url" -o "$RUNNER_TEMP/gelezen"
        if cmp -s "release/$bestand" "$RUNNER_TEMP/gelezen"; then
            continue
        fi
        [[ "$concept" == true ]]
        api --fail -X DELETE "$basis/releases/$id/assets/$asset" >/dev/null
    fi
    [[ "$concept" == true ]]
    api --fail -F "attachment=@release/$bestand" "$basis/releases/$id/assets?name=$bestand" |
        jq -e --arg naam "$bestand" '.name == $naam' >/dev/null
    api --fail --location "$url" -o "$RUNNER_TEMP/gelezen"
    cmp "release/$bestand" "$RUNNER_TEMP/gelezen"
done
if [[ "$concept" == true ]]; then
    jq -n --rawfile tekst "$RUNNER_TEMP/releasenotities" '{draft:false,body:$tekst}' |
        api --fail -X PATCH -H 'Content-Type: application/json' --data-binary @- "$basis/releases/$id" |
        jq -e '.draft == false' >/dev/null
fi
