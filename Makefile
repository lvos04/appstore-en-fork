# Ontwikkeltaken (niet nodig voor gebruikers; installeren gaat via de Custom App / de Hub).
PY ?= .venv/bin/python
API := apps/api
IMAGE ?= appstore-en-fork:dev

.PHONY: venv lint typecheck test-unit test-integration test-e2e test openapi manifest image image-e2e clean

venv:
	python3.12 -m venv .venv && $(PY) -m pip install -q -e "$(API)[dev]"

lint:
	cd $(API) && ../../$(PY) -m ruff check src tests alembic && ../../$(PY) -m ruff format --check src tests alembic
	$(PY) -m ruff check tests/e2e deploy/allinone

typecheck:
	cd $(API) && ../../$(PY) -m mypy

test-unit:
	cd $(API) && ../../$(PY) -m pytest -q tests/unit

test-integration:
	cd $(API) && ../../$(PY) -m pytest -q tests/integration

test-e2e:
	$(PY) -m pytest -q tests/e2e

test: test-unit test-integration test-e2e

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
