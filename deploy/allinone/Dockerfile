# syntax=docker/dockerfile:1.7
# appstore-en-fork — ALLES-IN-ÉÉN image (ingebouwde PostgreSQL 16 + API + worker) voor een standaard TrueNAS Custom App.
#
#   docker build -f deploy/allinone/Dockerfile [--secret id=ca_bundle,src=<ca.crt>] -t appstore-en-fork:dev .
#
# Installeren: TrueNAS → Apps → Discover Apps → Custom App (formulier; nooit YAML). Zie deploy/truenas/CUSTOM_APP.md.
# Eén poort 30430, één opslag /appdata, géén verplichte omgevingsvariabelen en géén wachtwoorden in het formulier.
# PostgreSQL luistert alleen op een unix-socket. De container start als root uitsluitend om /appdata aan 568:568 te
# geven; daarna draaien alle processen als 568 met no_new_privs en zonder capabilities.
#
# Base-images op exacte versie én digest (contract §1); bijwerken: docker buildx imagetools inspect <image>:<tag>.
ARG PYTHON_IMAGE=mirror.gcr.io/library/python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ARG POSTGRES_IMAGE=mirror.gcr.io/library/postgres:16.15-bookworm@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9

# ---------------------------------------------------------------- PostgreSQL 16 (server + client) uit de officiële image
FROM ${POSTGRES_IMAGE} AS pg
RUN set -eu; mkdir -p /out/lib /out/pg; \
    rm -f /usr/lib/postgresql/16/lib/llvmjit*.so; rm -rf /usr/lib/postgresql/16/lib/bitcode; \
    for f in /usr/lib/postgresql/16/bin/* /usr/lib/postgresql/16/lib/*.so; do ldd "$f" 2>/dev/null || true; done \
      | awk '/=>/ {print $3}' | sort -u \
      | grep -vE '/(libc|libm|libdl|libpthread|librt|libresolv|ld-linux[^/]*)\.so' \
      | while read -r lib; do cp -L "$lib" /out/lib/; done; \
    cp -rL /usr/lib/postgresql/16 /out/pg/lib; cp -rL /usr/share/postgresql/16 /out/pg/share; \
    test -f /out/pg/share/postgresql.conf.sample

# ---------------------------------------------------------------- Python-pakket (vastgezette afhankelijkheden met hashes)
FROM ${PYTHON_IMAGE} AS build
WORKDIR /src
COPY apps/api/requirements.lock /src/requirements.lock
COPY deploy/allinone/tools.lock /src/tools.lock
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore --prefix=/install \
        --require-hashes -r requirements.lock -r tools.lock

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE}
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="appstore-en-fork" \
      org.opencontainers.image.description="Appstore-catalogus TrueNAS Home Platform V2 — alles-in-één (PostgreSQL 16, API, worker)" \
      org.opencontainers.image.source="https://github.com/lvos04/appstore-en-fork" \
      org.opencontainers.image.version="${VERSION}"
COPY --from=pg /out/pg/lib /usr/lib/postgresql/16
COPY --from=pg /out/pg/share /usr/share/postgresql/16
COPY --from=pg /out/lib /opt/pglibs
COPY --from=build /install /usr/local
# eigen pakket als bron (geen build-tools nodig); commando's via `python -m appstore …`
COPY apps/api/src/appstore /usr/local/lib/python3.12/site-packages/appstore
COPY apps/api/alembic /usr/local/share/appstore/alembic
COPY deploy/manifest/appstore-en-fork.manifest.yaml /usr/local/share/appstore/catalog/appstore-en-fork.manifest.yaml
COPY catalog/bundled/ /usr/local/share/appstore/catalog/
COPY deploy/allinone/supervisor.py deploy/allinone/healthcheck.py /usr/local/lib/appstore/
RUN set -eu; \
    for b in postgres initdb pg_ctl pg_isready psql pg_dump pg_restore; do \
      printf '#!/bin/sh\nLD_LIBRARY_PATH=/opt/pglibs exec /usr/lib/postgresql/16/bin/%s "$@"\n' "$b" > /usr/local/bin/$b; \
      chmod 0755 /usr/local/bin/$b; \
    done; \
    postgres --version | grep -q ' 16\.'; pg_dump --version | grep -q ' 16\.'; \
    groupadd -g 568 apps; useradd -u 568 -g 568 -M -d /tmp -s /usr/sbin/nologin apps; \
    mkdir -p /appdata; chown 568:568 /appdata; \
    find /usr/local/share/appstore -type d -exec chmod 0755 {} +; find /usr/local/share/appstore -type f -exec chmod 0644 {} +; \
    python -c "import appstore.app, psycopg, uvicorn, cryptography, dulwich; print('ok')"; \
    rm -rf /root/.cache
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    APPSTORE_DATA_DIR=/appdata APPSTORE_PORT=30430 \
    APPSTORE_ALEMBIC_DIR=/usr/local/share/appstore/alembic \
    APPSTORE_BUNDLED_CATALOG_DIR=/usr/local/share/appstore/catalog \
    APPSTORE_DATABASE_URL="postgresql+psycopg://appstore@/appstore?host=/tmp/appstore-pg"
VOLUME ["/appdata"]
EXPOSE 30430
HEALTHCHECK --interval=30s --timeout=8s --start-period=90s --retries=3 \
  CMD ["python", "/usr/local/lib/appstore/healthcheck.py"]
ENTRYPOINT ["/usr/local/bin/dumb-init", "--"]
CMD ["python", "/usr/local/lib/appstore/supervisor.py"]
