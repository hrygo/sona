"""会议运行时/API 的可读 DisplayBlock 兼容契约验收。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker

from sona.meeting.api import _transcript_json
from sona.meeting.events import make_event
from sona.meeting.transcript_models import DisplayBlock

MEETING_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _block(*, text: str = "完整发言") -> DisplayBlock:
    return DisplayBlock(
        block_id="block-1",
        item_ids=(uuid4(),),
        source_ids=("item-1#unit-1",),
        text=text,
        start_ms=0,
        end_ms=1_000,
        speaker_key="speaker-1",
        speaker_name="说话人 1",
        speaker_status="anonymous",
        speaker_color_token="speaker-12345678",
        timing_quality="aligned",
    )


def test_transcript_json_adds_display_blocks_and_preserves_legacy_segments() -> None:
    document = SimpleNamespace(
        meeting_id=MEETING_ID,
        transcript_revision=2,
        content_revision=3,
        segments=(),
        speakers=(),
    )

    payload = _transcript_json(document, display_blocks=(_block(),))

    assert payload["segments"][0]["text"] == "完整发言"
    assert payload["display_blocks"][0]["block_id"] == "block-1"
    assert payload["display_blocks"][0]["source_ids"] == ["item-1#unit-1"]
    assert "spans" not in payload["display_blocks"][0]


def test_transcript_json_keeps_timing_unavailable_as_unknown_time() -> None:
    block = _block().model_copy(
        update={"start_ms": None, "end_ms": None, "timing_quality": "unavailable"}
    )
    document = SimpleNamespace(
        meeting_id=MEETING_ID,
        transcript_revision=0,
        content_revision=0,
        segments=(),
        speakers=(),
    )

    payload = _transcript_json(document, display_blocks=(block,))

    assert payload["display_blocks"][0]["start_ms"] is None
    assert payload["display_blocks"][0]["timing_quality"] == "unavailable"


def test_reconciled_event_schema_accepts_display_blocks() -> None:
    block = _display_block_json_for_contract()
    event = make_event(
        "transcript_reconciled",
        MEETING_ID,
        {
            "transcript_revision": 1,
            "content_revision": 1,
            "replace_from_ms": 0,
            "segments": [],
            "display_blocks": [block],
        },
    )
    # 通过 API helper 生成的字段应与 v1 event schema 一致。
    Draft202012Validator(
        json.loads(
            Path(
                "contracts/meeting-assistant/v1/schemas/event-transcript-reconciled.schema.json"
            ).read_text(encoding="utf-8")
        ),
        format_checker=FormatChecker(),
    ).validate(event)


def _display_block_json_for_contract() -> dict[str, object]:
    return _transcript_json(
        SimpleNamespace(
            meeting_id=MEETING_ID,
            transcript_revision=1,
            content_revision=1,
            segments=(),
            speakers=(),
        ),
        display_blocks=(_block(),),
    )["display_blocks"][0]
