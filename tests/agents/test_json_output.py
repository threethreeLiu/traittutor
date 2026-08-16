from __future__ import annotations

import json

import pytest

from traittutor.agents._shared.json_output import parse_strict_json_object


def test_strict_json_object_accepts_raw_object() -> None:
    assert parse_strict_json_object('  {"status": "ok"}\n') == {"status": "ok"}


def test_strict_json_object_accepts_exact_json_fence() -> None:
    assert parse_strict_json_object('```JSON\n{"status": "ok"}\n```') == {"status": "ok"}


@pytest.mark.parametrize(
    "value",
    [
        'Here is the result:\n```json\n{"status": "ok"}\n```',
        '```json\n{"status": "ok"}\n```\nExtra text',
        '```text\n{"status": "ok"}\n```',
        '```json\n{"first": true}\n```\n```json\n{"second": true}\n```',
        '[{"status": "ok"}]',
        '{"status":',
    ],
)
def test_strict_json_object_rejects_ambiguous_or_incomplete_output(value: str) -> None:
    with pytest.raises((json.JSONDecodeError, ValueError)):
        parse_strict_json_object(value)
