"""历史 transcript_segments 对账计划的纯函数验收。"""

from uuid import UUID, uuid4

from sona.meeting.transcript_reconciliation import (
    LegacyTranscriptRow,
    build_reconciliation_report,
)

MEETING_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _row(
    order: int,
    text: str,
    *,
    uid: str | None = None,
    item_id: str | None = "item-1",
    session_id: str | None = "session-1",
) -> LegacyTranscriptRow:
    return LegacyTranscriptRow(
        row_id=uuid4(),
        meeting_id=MEETING_ID,
        segment_order=order,
        source_epoch=1,
        source_session_id=session_id,
        source_segment_uid=uid or f"unit-{order}",
        source_item_id=item_id,
        start_ms=order * 100,
        end_ms=(order + 1) * 100,
        text=text,
        speaker_key="__unknown__",
        speaker_status="unknown",
        timing_quality="aligned",
    )


def test_report_preserves_source_text_and_is_resumable() -> None:
    report = build_reconciliation_report(
        [_row(0, "你好"), _row(1, "世界")], next_cursor="cursor-2"
    )

    assert report.ready is True
    assert report.item_count == 1
    assert report.items[0].text == "你好世界"
    assert [span.text for span in report.items[0].spans] == ["你好", "世界"]
    assert report.next_cursor == "cursor-2"


def test_report_rejects_duplicate_uid_and_missing_identity() -> None:
    report = build_reconciliation_report(
        [
            _row(0, "甲", uid="same"),
            _row(1, "乙", uid="same"),
            _row(2, "丙", session_id=None),
        ],
        next_cursor=None,
    )

    assert report.ready is False
    assert len(report.duplicate_uids) == 1
    assert report.missing_identity_rows == 1
    assert report.item_count == 1


def test_report_rejects_empty_source_text() -> None:
    report = build_reconciliation_report([_row(0, "   ")], next_cursor=None)

    assert report.ready is False
    assert report.text_conservation_failures == 1
    assert report.item_count == 0
