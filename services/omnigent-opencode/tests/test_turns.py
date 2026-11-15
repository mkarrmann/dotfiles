"""TurnTracker over event orders the T3 Code client tests cannot script cheaply."""

from __future__ import annotations

from omnigent_opencode.turns import ReplyCommitted, TextDelta, TurnOutput, TurnTracker

CHUNKS = ["Findings.\n\n", "1. Fix it.\n", "2. Test it.\n\n", "Should I go ahead?"]


def chunk(index: int, message_id: str = "m1") -> tuple[str, dict[str, object]]:
    return (
        "response.output_text.delta",
        {"delta": CHUNKS[index], "message_id": message_id, "index": index, "final": index == len(CHUNKS) - 1},
    )


def committed(text: str) -> tuple[str, dict[str, object]]:
    return (
        "response.output_item.done",
        {"item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}},
    )


def replies(events: list[tuple[str, dict[str, object]]]) -> list[str]:
    """The text of each reply T3 Code would show, split where the tracker commits."""
    tracker = TurnTracker()
    outputs: list[TurnOutput] = [o for event_type, data in events for o in tracker.feed(event_type, data)]
    texts, current = [], ""
    for output in outputs:
        if isinstance(output, TextDelta):
            current += output.text
        elif isinstance(output, ReplyCommitted):
            texts.append(current)
            current = ""
    return texts + ([current] if current else [])


def test_a_chunk_streamed_after_its_reply_committed_stays_in_that_reply() -> None:
    # claude-native forwards the transcript's message before the hook has written the last chunk.
    assert replies([chunk(0), chunk(1), chunk(2), committed("".join(CHUNKS)), chunk(3)]) == ["".join(CHUNKS)]


def test_chunks_in_order_make_one_reply() -> None:
    assert replies([chunk(0), chunk(1), chunk(2), chunk(3), committed("".join(CHUNKS))]) == ["".join(CHUNKS)]


def test_the_next_message_after_a_commit_is_a_reply_of_its_own() -> None:
    events = [chunk(0), chunk(1), chunk(2), chunk(3), committed("".join(CHUNKS)), chunk(0, "m2"), committed(CHUNKS[0])]
    assert replies(events) == ["".join(CHUNKS), CHUNKS[0]]
