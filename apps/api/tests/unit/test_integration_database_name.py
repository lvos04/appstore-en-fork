"""De integratiedatabase mag niet botsen bij hergebruik van een proces-ID."""

import re

from tests.integration.conftest import _database_name


def test_database_names_are_distinct_within_one_process() -> None:
    first, second = _database_name(), _database_name()
    assert first != second
    assert re.fullmatch(r"appstore_test_\d+_[0-9a-f]{32}", first)
    assert len(first) <= 63  # PostgreSQL kapt langere namen af.
