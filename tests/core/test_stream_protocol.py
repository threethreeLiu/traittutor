"""Contract tests for the canonical stream event envelope."""

from traittutor.core.stream import StreamEvent, StreamEventType


def test_stream_event_serializes_canonical_envelope() -> None:
    event = StreamEvent(
        type=StreamEventType.CONTENT,
        source="learning",
        stage="explain",
        content="A concise explanation.",
        metadata={"format": "markdown"},
        request_id="req-123",
    )

    payload = event.to_dict()

    assert payload["event_id"].startswith("evt_")
    assert payload["request_id"] == "req-123"
    assert payload["type"] == "content"
    assert payload["data"] == {
        "content": "A concise explanation.",
        "stage": "explain",
        "metadata": {"format": "markdown"},
    }
