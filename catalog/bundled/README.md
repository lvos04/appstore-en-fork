# Ingebouwde catalogus

Manifesten (`*.manifest.yaml`, manifeststandaard v1) die in de image worden meegeleverd en bij elke start als bron
`ingebouwd` worden gesynchroniseerd. Het eigen manifest staat in `deploy/manifest/` en wordt er bij het bouwen bij
gekopieerd. Manifesten van andere apps worden pas toegevoegd als hun gegevens (image, digest, poorten, datasets) uit
de echte TrueNAS-inventaris of de releases van die apps komen — nooit met voorbeeldwaarden.
