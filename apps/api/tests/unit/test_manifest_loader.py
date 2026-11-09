"""Veilige YAML-lader."""

from __future__ import annotations

from appstore.manifest.loader import MAX_MANIFEST_BYTES, load_manifest_text


def codes(text: str | bytes) -> list[str]:
    return [f.code for f in load_manifest_text(text)[1]]


def test_anchors_and_aliases_refused() -> None:
    assert codes("a: &x {b: 1}\nc: *x\n") == ["yaml_alias_forbidden"]


def test_duplicate_keys_refused() -> None:
    assert codes("app_id: a\napp_id: b\n") == ["yaml_duplicate_key"]


def test_custom_tags_refused() -> None:
    assert codes("a: !!python/object:os.system {}\n") == ["yaml_custom_tag_forbidden"]
    assert codes("a: !eigen 1\n") == ["yaml_custom_tag_forbidden"]


def test_size_limit() -> None:
    assert codes("a: " + "x" * (MAX_MANIFEST_BYTES + 1)) == ["manifest_too_large"]


def test_invalid_utf8_and_yaml() -> None:
    assert codes(b"\xff\xfe") == ["yaml_invalid"]
    assert codes("a: [1, 2") == ["yaml_invalid"]


def test_not_mapping() -> None:
    assert codes("- 1\n- 2\n") == ["manifest_not_mapping"]


def test_dates_stay_text() -> None:
    data, findings = load_manifest_text("version: 2026-11-09\n")
    assert findings == [] and data == {"version": "2026-11-09"}


def test_json_is_accepted() -> None:
    data, findings = load_manifest_text('{"app_id": "x"}')
    assert findings == [] and data == {"app_id": "x"}
