# Ontwikkeltaken (niet nodig voor gebruikers; installeren gaat via de Custom App / de Hub).
PY ?= .venv/bin/python
API := apps/api
IMAGE ?= appstore-en-fork:dev

.PHONY: venv lint typecheck test-unit test-integration test-e2e test test-ui schermafdrukken peil-drift openapi manifest image image-e2e clean

venv:
	python3.12 -m venv .venv && $(PY) -m pip install -q -e "$(API)[dev]"

lint:
	cd $(API) && ../../$(PY) -m ruff check src tests alembic && ../../$(PY) -m ruff format --check src tests alembic
	$(PY) -m ruff check tests/e2e tests/ui deploy/allinone tools && $(PY) -m ruff format --check tests/ui tools

typecheck:
	cd $(API) && ../../$(PY) -m mypy

test-unit:
	cd $(API) && ../../$(PY) -m pytest -q tests/unit

test-integration:
	cd $(API) && ../../$(PY) -m pytest -q tests/integration

test-e2e:
	$(PY) -m pytest -q tests/e2e

test: test-unit test-integration test-e2e

# statuspagina in Peil: toestanden uit de echte app, dan axe/overloop/CSP in Chromium (eenmalig: cd tests/ui && npm ci)
test-ui:
	$(PY) tests/ui/genereer_toestanden.py --uit tests/ui/uit
	cd tests/ui && npx playwright test

schermafdrukken:
	$(PY) tests/ui/genereer_toestanden.py --uit tests/ui/uit
	cd tests/ui && SCHERMAFDRUKKEN=1 npx playwright test

# versie-gepinde Peil-kopie tegen de bron (ai-server-manager op GitHub, en de checkout ernaast als die er is)
peil-drift:
	python3 tools/peil_kopie.py vergelijk --online $$( [ -d ../ai-server-manager/design/ontwerptaal ] && echo --bron ../ai-server-manager )

openapi:
	$(PY) -m appstore openapi --out docs/openapi/appstore-en-fork.openapi.json

manifest:
	$(PY) -m appstore.manifest.cli validate deploy/manifest/appstore-en-fork.manifest.yaml

image:
	nice -n 10 docker build -f deploy/allinone/Dockerfile -t $(IMAGE) .

image-e2e:
	APPSTORE_E2E_IMAGE=$(IMAGE) $(PY) -m pytest -q tests/e2e/test_allinone_image.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; rm -rf $(API)/.pytest_cache $(API)/.mypy_cache $(API)/.ruff_cache
