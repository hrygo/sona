"""旧 transcript_segments 到新正文/归属事实的可审计对账计划。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LegacyTranscriptRow:
    """旧表中一行可用于对账的事实。"""

    row_id: UUID
    meeting_id: UUID
    segment_order: int
    source_epoch: int
    source_session_id: str | None
    source_segment_uid: str | None
    source_item_id: str | None
    start_ms: int
    end_ms: int
    text: str
    speaker_key: str
    speaker_status: str
    timing_quality: str


@dataclass(frozen=True, slots=True)
class ReconciliationItem:
    """一组旧 segment 合成一条新正文及其 spans。"""

    meeting_id: UUID
    source_session_id: str
    source_epoch: int
    source_item_id: str
    sequence: int
    text: str
    start_ms: int
    end_ms: int
    spans: tuple[LegacyTranscriptRow, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """可序列化的对账结果；未 ready 时禁止 apply。"""

    source_rows: int
    item_count: int
    duplicate_uids: tuple[str, ...]
    missing_identity_rows: int
    text_conservation_failures: int
    items: tuple[ReconciliationItem, ...]
    next_cursor: str | None

    @property
    def ready(self) -> bool:
        return (
            not self.duplicate_uids
            and self.missing_identity_rows == 0
            and self.text_conservation_failures == 0
        )


def build_reconciliation_report(
    rows: list[LegacyTranscriptRow], *, next_cursor: str | None
) -> ReconciliationReport:
    """按 source item 分组，校验 UID 唯一性与正文守恒。"""
    duplicate_uids: list[str] = []
    seen_uids: set[tuple[UUID, str, str]] = set()
    missing_identity_rows = 0
    groups: defaultdict[tuple[UUID, str, str], list[LegacyTranscriptRow]] = defaultdict(list)
    for row in rows:
        if not row.source_session_id or not row.source_segment_uid or not row.source_item_id:
            missing_identity_rows += 1
            continue
        uid_key = (row.meeting_id, row.source_session_id, row.source_segment_uid)
        if uid_key in seen_uids:
            duplicate_uids.append(":".join(str(value) for value in uid_key))
        seen_uids.add(uid_key)
        groups[(row.meeting_id, row.source_session_id, row.source_item_id)].append(row)

    items: list[ReconciliationItem] = []
    text_conservation_failures = 0
    for (meeting_id, session_id, item_id), group in sorted(
        groups.items(), key=lambda entry: min(row.segment_order for row in entry[1])
    ):
        ordered = tuple(
            sorted(group, key=lambda row: (row.segment_order, row.start_ms, row.row_id))
        )
        text = "".join(row.text for row in ordered)
        if not text.strip():
            text_conservation_failures += 1
            continue
        items.append(
            ReconciliationItem(
                meeting_id=meeting_id,
                source_session_id=session_id,
                source_epoch=ordered[0].source_epoch,
                source_item_id=item_id,
                sequence=ordered[0].segment_order,
                text=text,
                start_ms=min(row.start_ms for row in ordered),
                end_ms=max(row.end_ms for row in ordered),
                spans=ordered,
            )
        )

    return ReconciliationReport(
        source_rows=len(rows),
        item_count=len(items),
        duplicate_uids=tuple(sorted(set(duplicate_uids))),
        missing_identity_rows=missing_identity_rows,
        text_conservation_failures=text_conservation_failures,
        items=tuple(items),
        next_cursor=next_cursor,
    )


__all__ = [
    "LegacyTranscriptRow",
    "ReconciliationItem",
    "ReconciliationReport",
    "build_reconciliation_report",
]
