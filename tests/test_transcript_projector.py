"""统一转录事实与展示投影的领域验收。"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from sona.meeting.model_transcript import build_model_transcript
from sona.meeting.transcript_models import (
    DisplayBlock,
    ProjectorProfile,
    TranscriptAttributionSpan,
    TranscriptItem,
)
from sona.meeting.transcript_projector import TranscriptPresentationProjector

MEETING_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _item(
    text: str,
    *,
    sequence: int,
    start_ms: int,
    end_ms: int,
    source_item_id: str = "item-1",
    source_segment_uid: str | None = None,
) -> TranscriptItem:
    return TranscriptItem(
        id=uuid4(),
        meeting_id=MEETING_ID,
        source_session_id="session-1",
        source_epoch=1,
        source_item_id=source_item_id,
        source_segment_uid=source_segment_uid or f"segment-{sequence}",
        sequence=sequence,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        language="zh",
    )


def _span(
    item: TranscriptItem,
    *,
    speaker_key: str | None = "speaker-1",
    speaker_status: str = "anonymous",
    speaker_name: str | None = None,
    timing_quality: str = "aligned",
) -> TranscriptAttributionSpan:
    return TranscriptAttributionSpan(
        id=uuid4(),
        item_id=item.id,
        text_start=0,
        text_end=len(item.text),
        audio_start_ms=item.start_ms,
        audio_end_ms=item.end_ms,
        timing_quality=timing_quality,  # type: ignore[arg-type]
        speaker_key=speaker_key,
        speaker_status=speaker_status,  # type: ignore[arg-type]
        speaker_name=speaker_name,
    )


def test_transcript_item_preserves_completed_text_and_is_immutable() -> None:
    item = _item("  原文 不应被 trim  ", sequence=0, start_ms=0, end_ms=100)

    assert item.text == "  原文 不应被 trim  "
    with pytest.raises(ValidationError):
        item.text = "被篡改"  # type: ignore[misc]


def test_transcript_span_requires_valid_non_empty_range_and_item_validation() -> None:
    item = _item("你好", sequence=0, start_ms=0, end_ms=100)
    invalid = TranscriptAttributionSpan(
        id=uuid4(),
        item_id=item.id,
        text_start=1,
        text_end=3,
        audio_start_ms=0,
        audio_end_ms=100,
        timing_quality="aligned",
        speaker_key="speaker-1",
        speaker_status="anonymous",
    )

    with pytest.raises(ValueError, match="text range"):
        TranscriptPresentationProjector.validate_spans(item, (invalid,))


def test_projector_merges_same_source_item_until_gap_or_limit() -> None:
    first = _item("你好", sequence=0, start_ms=0, end_ms=500)
    second = _item("世界。", sequence=1, start_ms=600, end_ms=1_000)
    spans = (_span(first), _span(second))

    blocks = TranscriptPresentationProjector().project((first, second), spans)

    assert [block.text for block in blocks] == ["你好世界。"]
    assert blocks[0].item_ids == (first.id, second.id)


def test_projector_breaks_on_gap_duration_length_and_speaker_change() -> None:
    items = (
        _item("甲", sequence=0, start_ms=0, end_ms=100),
        _item("乙", sequence=1, start_ms=1_301, end_ms=1_400),
        _item("丙", sequence=2, start_ms=1_500, end_ms=1_600),
    )
    spans = (
        _span(items[0], speaker_key="speaker-1"),
        _span(items[1], speaker_key="speaker-1"),
        _span(items[2], speaker_key="speaker-2"),
    )

    blocks = TranscriptPresentationProjector().project(items, spans)

    assert [block.text for block in blocks] == ["甲", "乙", "丙"]


@pytest.mark.parametrize(
    ("profile", "texts", "expected"),
    [
        (ProjectorProfile(max_chars=5), ("12345", "6"), ("12345", "6")),
        (
            ProjectorProfile(max_duration_ms=15_000),
            ("起点", "中段", "超出"),
            ("起点中段", "超出"),
        ),
    ],
)
def test_projector_applies_exact_length_and_duration_limits(
    profile: ProjectorProfile,
    texts: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    if profile.max_duration_ms == 15_000 and profile.max_chars == 180:
        items = (
            _item(texts[0], sequence=0, start_ms=0, end_ms=1_000),
            _item(texts[1], sequence=1, start_ms=1_001, end_ms=15_000),
            _item(texts[2], sequence=2, start_ms=15_001, end_ms=15_001),
        )
    else:
        items = tuple(
            _item(text, sequence=index, start_ms=index * 100, end_ms=index * 100 + 50)
            for index, text in enumerate(texts)
        )

    spans = tuple(_span(item) for item in items)
    blocks = TranscriptPresentationProjector().project(items, spans, profile=profile)

    assert tuple(block.text for block in blocks) == expected


def test_projector_breaks_after_strong_punctuation_and_across_source_items() -> None:
    first = _item("结束。", sequence=0, start_ms=0, end_ms=100, source_item_id="item-1")
    second = _item("下一句", sequence=1, start_ms=101, end_ms=200, source_item_id="item-1")
    third = _item("另一个 item", sequence=2, start_ms=201, end_ms=300, source_item_id="item-2")

    blocks = TranscriptPresentationProjector().project(
        (first, second, third), tuple(_span(item) for item in (first, second, third))
    )

    assert tuple(block.text for block in blocks) == ("结束。", "下一句", "另一个 item")


def test_projector_keeps_text_conservation_and_one_partial_bottom_block() -> None:
    items = (
        _item("第一句", sequence=0, start_ms=0, end_ms=500),
        _item("第二句", sequence=1, start_ms=600, end_ms=1_000),
    )
    spans = tuple(_span(item) for item in items)

    blocks = TranscriptPresentationProjector().project(
        items,
        spans,
        partial_text="正在说",
        partial_speaker_key="speaker-1",
    )

    assert "".join(block.text for block in blocks[:-1]) == "第一句第二句"
    assert blocks[-1].text == "正在说"
    assert blocks[-1].is_partial is True
    assert sum(block.is_partial for block in blocks) == 1


def test_projector_reports_unavailable_timing_without_pseudo_precision() -> None:
    item = _item("无时间定位", sequence=0, start_ms=100, end_ms=200)

    block = TranscriptPresentationProjector().project(
        (item,), (_span(item, timing_quality="unavailable"),)
    )[0]

    assert block.timing_quality == "unavailable"
    assert block.start_ms is None
    assert block.end_ms is None


def test_projector_uses_semantic_speaker_status_and_color_token() -> None:
    item = _item("稳定匿名说话人", sequence=0, start_ms=0, end_ms=100)
    block = TranscriptPresentationProjector().project(
        (item,), (_span(item, speaker_status="identified", speaker_name="主持人"),)
    )[0]

    assert block.speaker_status == "identified"
    assert block.speaker_name == "主持人"
    assert block.speaker_color_token.startswith("speaker-")


def test_model_transcript_uses_block_level_evidence_aliases() -> None:
    item = _item("完整发言，而不是一个字一条 evidence", sequence=0, start_ms=0, end_ms=100)
    blocks = TranscriptPresentationProjector().project((item,), (_span(item),))

    transcript = build_model_transcript(blocks)

    assert transcript.text.startswith("[B0001]")
    assert transcript.evidence[0].alias == "B0001"
    assert transcript.evidence[0].text == item.text
    assert transcript.evidence[0].source_ids == (
        f"{item.source_item_id}#{item.source_segment_uid}",
    )
    assert all(len(evidence.text) > 1 for evidence in transcript.evidence)


def test_display_block_is_frozen_and_has_stable_shape() -> None:
    block = DisplayBlock(
        block_id="block-1",
        item_ids=(uuid4(),),
        source_ids=("segment-1",),
        text="完整",
        start_ms=0,
        end_ms=100,
        speaker_key=None,
        speaker_name=None,
        speaker_status="pending",
        speaker_color_token="speaker-neutral",
        timing_quality="aligned",
        is_partial=False,
    )

    assert block.is_partial is False
    with pytest.raises(ValidationError):
        block.text = "修改"  # type: ignore[misc]
