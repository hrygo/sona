#!/usr/bin/env python3
"""对账旧 transcript_segments；默认 dry-run，--apply 才写入新事实表。"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import UUID, uuid5

from psycopg import AsyncConnection

from sona.meeting.migrations import validate_schema_name
from sona.meeting.transcript_reconciliation import (
    LegacyTranscriptRow,
    ReconciliationItem,
    ReconciliationReport,
    build_reconciliation_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default="postgresql:///knowledge")
    parser.add_argument("--schema", default="sona")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--after-id", type=UUID)
    parser.add_argument("--apply", action="store_true", help="写入新事实表；默认仅 dry-run")
    return parser


async def _load_rows(
    connection: Any, schema: str, *, batch_size: int, after_id: UUID | None
) -> tuple[list[LegacyTranscriptRow], UUID | None]:
    """按完整 source item 分页，避免一个 item 被截断到两个 apply 批次。"""
    group_cursor = await connection.execute(
        f"""
        SELECT meeting_id, source_session_id, source_item_id
        FROM "{schema}".transcript_segments
        WHERE source_session_id IS NOT NULL
          AND source_session_id <> ''
          AND source_item_id IS NOT NULL
          AND source_item_id <> ''
          AND (%s::uuid IS NULL OR id > %s::uuid)
        GROUP BY meeting_id, source_session_id, source_item_id
        ORDER BY max(id::text)
        LIMIT %s
        """,
        (after_id, after_id, batch_size),
    )
    groups = await group_cursor.fetchall()
    rows_by_id: dict[UUID, LegacyTranscriptRow] = {}
    if groups:
        values = ", ".join("(%s, %s, %s)" for _ in groups)
        group_params = [value for group in groups for value in group]
        cursor = await connection.execute(
            f"""
            SELECT rows.id, rows.meeting_id, rows.segment_order, rows.source_epoch,
                   rows.source_session_id, rows.source_segment_uid,
                   rows.source_item_id, rows.start_ms, rows.end_ms, rows.text,
                   rows.speaker_key, rows.speaker_status, rows.timing_quality
            FROM "{schema}".transcript_segments AS rows
            JOIN (VALUES {values}) AS selected(
                meeting_id, source_session_id, source_item_id
            )
              ON rows.meeting_id = selected.meeting_id
             AND rows.source_session_id = selected.source_session_id
             AND rows.source_item_id = selected.source_item_id
            ORDER BY rows.id
            """,
            group_params,
        )
        rows_by_id.update(
            {
                row[0]: LegacyTranscriptRow(*row)
                for row in await cursor.fetchall()
            }
        )

    invalid_cursor = await connection.execute(
        f"""
        SELECT id, meeting_id, segment_order, source_epoch, source_session_id,
               source_segment_uid, source_item_id, start_ms, end_ms, text,
               speaker_key, speaker_status, timing_quality
        FROM "{schema}".transcript_segments
        WHERE (
            source_session_id IS NULL OR source_session_id = ''
            OR source_segment_uid IS NULL OR source_segment_uid = ''
            OR source_item_id IS NULL OR source_item_id = ''
        )
          AND (%s::uuid IS NULL OR id > %s::uuid)
        ORDER BY id
        LIMIT %s
        """,
        (after_id, after_id, batch_size),
    )
    rows_by_id.update(
        {
            row[0]: LegacyTranscriptRow(*row)
            for row in await invalid_cursor.fetchall()
        }
    )
    rows = sorted(rows_by_id.values(), key=lambda row: row.row_id)
    return rows, max((row.row_id for row in rows), default=None)


async def _apply_item(connection: Any, schema: str, item: ReconciliationItem) -> None:
    item_id = uuid5(
        item.meeting_id,
        f"legacy-transcript-item:{item.source_session_id}:{item.source_item_id}",
    )
    await connection.execute(
        f"""
        INSERT INTO "{schema}".transcript_items
            (id, meeting_id, source_session_id, source_epoch, source_item_id,
             source_segment_uid, sequence, start_ms, end_ms, text, language)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'und')
        ON CONFLICT (meeting_id, source_session_id, source_item_id) DO NOTHING
        """,
        (
            item_id,
            item.meeting_id,
            item.source_session_id,
            item.source_epoch,
            item.source_item_id,
            f"item:{item.source_item_id}",
            item.sequence,
            item.start_ms,
            item.end_ms,
            item.text,
        ),
    )
    offset = 0
    for span in item.spans:
        span_id = uuid5(item.meeting_id, f"{item.source_session_id}:{span.source_segment_uid}")
        speaker_key = (
            None
            if span.speaker_key in {"", "unknown", "__unknown__"}
            else span.speaker_key
        )
        status = "identified" if speaker_key else "pending"
        await connection.execute(
            f"""
            INSERT INTO "{schema}".transcript_attribution_spans
                (id, item_id, source_session_id, source_segment_uid,
                 text_start, text_end, audio_start_ms, audio_end_ms,
                 timing_quality, speaker_key, speaker_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (item_id, source_segment_uid) DO NOTHING
            """,
            (
                span_id,
                item_id,
                item.source_session_id,
                span.source_segment_uid,
                offset,
                offset + len(span.text),
                span.start_ms,
                span.end_ms,
                (
                    span.timing_quality
                    if span.timing_quality in {"aligned", "unavailable"}
                    else "unavailable"
                ),
                speaker_key,
                status,
            ),
        )
        offset += len(span.text)


def _report_json(report: ReconciliationReport, *, applied: int) -> str:
    return json.dumps(
        {
            "mode": "apply" if applied else "dry-run",
            "ready": report.ready,
            "source_rows": report.source_rows,
            "item_count": report.item_count,
            "duplicate_uids": report.duplicate_uids,
            "missing_identity_rows": report.missing_identity_rows,
            "text_conservation_failures": report.text_conservation_failures,
            "applied_items": applied,
            "next_cursor": report.next_cursor,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


async def _run(args: argparse.Namespace) -> int:
    schema = validate_schema_name(args.schema)
    if args.batch_size < 1 or args.batch_size > 10_000:
        raise SystemExit("--batch-size 必须在 1–10000 之间")
    async with await AsyncConnection.connect(args.dsn) as connection:
        rows, next_cursor_id = await _load_rows(
            connection, schema, batch_size=args.batch_size, after_id=args.after_id
        )
        next_cursor = str(next_cursor_id) if next_cursor_id is not None else None
        report = build_reconciliation_report(rows, next_cursor=next_cursor)
        applied = 0
        if args.apply:
            if not report.ready:
                print(_report_json(report, applied=0))
                return 2
            async with connection.transaction():
                for item in report.items:
                    await _apply_item(connection, schema, item)
                    applied += 1
        print(_report_json(report, applied=applied))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_parser().parse_args())))
