"""历史 transcript_segments 对账计划的纯函数和分页验收。"""

import importlib.util
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection

from sona.config import MeetingSettings
from sona.meeting.migrations import run_migrations
from sona.meeting.repository import PostgresMeetingRepository
from sona.meeting.transcript_reconciliation import (
    LegacyTranscriptRow,
    build_reconciliation_report,
)

MEETING_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _reconciliation_script():
    script_path = Path(__file__).parents[1] / "scripts" / "reconcile-transcript-items.py"
    spec = importlib.util.spec_from_file_location("reconcile_transcript_items", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


@pytest_asyncio.fixture
async def reconciliation_repository(tmp_path: Path):
    database_url = os.environ.get("SONA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SONA_TEST_DATABASE_URL 未设置；跳过真实 PostgreSQL 集成测试")
    schema = f"reconcile_test_{uuid4().hex[:16]}"
    async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
    await run_migrations(database_url, schema=schema)
    repository = PostgresMeetingRepository(
        MeetingSettings(
            database_url=database_url,
            schema=schema,
            recovery_dir=tmp_path / "recovery",
        )
    )
    await repository.open()
    meeting = await repository.create_meeting(
        "对账分页测试", language="Chinese", audio_source="microphone"
    )
    try:
        yield database_url, schema, meeting.id
    finally:
        await repository.close()
        async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.asyncio
async def test_reconciliation_page_keeps_item_rows_together(reconciliation_repository) -> None:
    database_url, schema, meeting_id = reconciliation_repository
    low_id = UUID("00000000-0000-0000-0000-000000000001")
    high_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    async with await AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute(
            f"""
            INSERT INTO "{schema}".transcript_segments
                (id, meeting_id, segment_order, source_epoch, speaker_key,
                 start_ms, end_ms, text, source_session_id, source_segment_uid,
                 source_item_id, speaker_status, timing_quality)
            VALUES
                (%s, %s, 0, 1, '__unknown__', 0, 100, '你',
                       'session-1', 'unit-0', 'item-1', 'unknown', 'aligned'),
                (%s, %s, 1, 1, '__unknown__', 100, 200, '好',
                       'session-1', 'unit-1', 'item-1', 'unknown', 'aligned')
            """,
            (low_id, meeting_id, high_id, meeting_id),
        )
        script = _reconciliation_script()
        rows, next_cursor = await script._load_rows(
            connection, schema, batch_size=1, after_id=None
        )
        assert [row.text for row in rows] == ["你", "好"]
        assert next_cursor == high_id

        remaining, remaining_cursor = await script._load_rows(
            connection, schema, batch_size=1, after_id=next_cursor
        )
        assert remaining == []
        assert remaining_cursor is None
