import pytest

from app.llm import ModelBackendError, parse_json_object


def test_parse_json_object_accepts_fenced_json():
    assert parse_json_object('```json\n{"items": []}\n```') == {"items": []}


def test_parse_json_object_extracts_object_from_short_preamble():
    assert parse_json_object('Result: {"product_id": "x"}') == {"product_id": "x"}


def test_parse_json_object_rejects_non_json():
    with pytest.raises(ModelBackendError):
        parse_json_object("no object here")

