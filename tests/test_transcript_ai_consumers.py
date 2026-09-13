"""summary / Inner OS 只消费完整 block 的转录事实。"""

from __future__ import annotations

from uuid import UUID

from sona.meeting.inner_os.context import build_context_snapshot
from sona.meeting.model_transcript import build_model_transcript
from sona.meeting.summary.evidence_anchor import _format_model_transcript, format_transcript
from sona.meeting.transcript_models import DisplayBlock

ITEM_ID = UUID("11111111-1111-4111-8111-111111111111")


def _blocks() -> tuple[DisplayBlock, ...]:
    return (
        DisplayBlock(
            block_id="block-1",
            item_ids=(ITEM_ID,),
            source_ids=("item-1#unit-1", "item-1#unit-2"),
            text="完整中文发言",
            start_ms=100,
            end_ms=800,
            speaker_key="speaker-1",
            speaker_name="说话人 1",
            speaker_status="anonymous",
            speaker_color_token="speaker-1",
            timing_quality="aligned",
        ),
    )


def test_summary_formats_model_transcript_with_block_aliases() -> None:
    model_transcript = build_model_transcript(_blocks())

    formatted, references = _format_model_transcript(model_transcript)

    assert formatted.startswith("[B0001]")
    assert "完整中文发言" in formatted
    assert "B0002" not in formatted
    assert references == {"B0001": ITEM_ID}
    assert format_transcript(model_transcript) == model_transcript.text


def test_inner_os_context_uses_block_level_evidence_and_focus_ids() -> None:
    model_transcript = build_model_transcript(_blocks())

    snapshot = build_context_snapshot(
        model_transcript,
        question="发言",
        focus_segment_ids=(ITEM_ID,),
    )

    assert snapshot.total_segment_count == 1
    assert snapshot.included_segment_count == 1
    assert snapshot.evidence[0].alias == "B0001"
    assert snapshot.evidence[0].segment_id == ITEM_ID
    assert snapshot.evidence[0].text == "完整中文发言"
