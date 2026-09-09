"""正文表和 attribution span 表的 PostgreSQL 集成验收。"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection

from sona.config import MeetingSettings
from sona.meeting.migrations import run_migrations
from sona.meeting.repository import PostgresMeetingRepository
from sona.meeting.speaker_attribution import (
    CompletedAttributionUnit,
    CompletedItem,
    SpeakerPatch,
    SpeakerPatchEvent,
)

MEETING_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _database_url() -> str:
    value = os.environ.get("SONA_TEST_DATABASE_URL")
    if not value:
        pytest.skip("SONA_TEST_DATABASE_URL 未设置；跳过真实 PostgreSQL 集成测试")
    return value


def _item(event_id: str = "event-1") -> CompletedItem:
    text = "你好世界"
    units = tuple(
        CompletedAttributionUnit(
            segment_uid=f"unit-{index}",
            text_start=index,
            text_end=index + 1,
            audio_start_sample=index * 160,
            audio_end_sample=(index + 1) * 160,
            timing_quality="aligned",
        )
        for index in range(len(text))
    )
    return CompletedItem(
        source_session_id="session-1",
        source_epoch=1,
        meeting_start_sample=0,
        item_id="item-1",
        event_id=event_id,
        sequence=0,
        audio_start_sample=0,
        audio_end_sample=len(text) * 160,
        canonical_text=text,
        units=units,
    )


@pytest_asyncio.fixture
async def repository(tmp_path: Path):
    database_url = _database_url()
    schema = f"tr_test_{uuid4().hex[:16]}"
    async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
    settings = MeetingSettings(
        database_url=database_url,
        schema=schema,
        recovery_dir=tmp_path / "recovery",
    )
    await run_migrations(database_url, schema=schema)
    repo = PostgresMeetingRepository(settings)
    await repo.open()
    meeting = await repo.create_meeting("正文表测试", language="Chinese", audio_source="microphone")
    try:
        yield repo, meeting.id, schema
    finally:
        await repo.close()
        async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.asyncio
async def test_migration_creates_item_span_and_revision_tables(repository) -> None:
    _, _, schema = repository
    database_url = _database_url()
    async with await AsyncConnection.connect(database_url) as connection:
        cursor = await connection.execute(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema = %s
                 AND table_name IN (
                   'transcript_items',
                   'transcript_attribution_spans',
                   'transcript_attribution_revisions'
                 )""",
            (schema,),
        )
        assert {row[0] for row in await cursor.fetchall()} == {
            "transcript_items",
            "transcript_attribution_spans",
            "transcript_attribution_revisions",
        }
        cursor = await connection.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = %s AND table_name = 'transcript_attribution_spans'""",
            (schema,),
        )
        assert "text" not in {row[0] for row in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_completed_item_writes_one_full_body_and_character_spans(repository) -> None:
    repo, meeting_id, _ = repository

    result = await repo.append_completed_item(meeting_id, _item())
    items = await repo.get_transcript_items(meeting_id)
    spans = await repo.get_transcript_attribution_spans(meeting_id)

    assert result is not None
    assert len(items) == 1
    assert items[0].text == "你好世界"
    assert items[0].source_item_id == "item-1"
    assert len(spans) == 4
    assert all(not hasattr(span, "text") for span in spans)


@pytest.mark.asyncio
async def test_repository_projects_new_facts_as_one_readable_block(repository) -> None:
    repo, meeting_id, _ = repository
    await repo.append_completed_item(meeting_id, _item())

    blocks = await repo.get_display_blocks(meeting_id)

    assert len(blocks) == 1
    assert blocks[0].text == "你好世界"
    assert blocks[0].speaker_status == "pending"
    assert len(blocks[0].source_ids) == 1


@pytest.mark.asyncio
async def test_replaying_completed_item_is_idempotent_for_new_tables(repository) -> None:
    repo, meeting_id, _ = repository
    item = _item()

    await repo.append_completed_item(meeting_id, item)
    second = await repo.append_completed_item(meeting_id, item)

    assert second is None
    assert len(await repo.get_transcript_items(meeting_id)) == 1
    assert len(await repo.get_transcript_attribution_spans(meeting_id)) == 4


@pytest.mark.asyncio
async def test_transcript_item_fact_columns_are_database_immutable(repository) -> None:
    repo, meeting_id, schema = repository
    await repo.append_completed_item(meeting_id, _item())
    item = (await repo.get_transcript_items(meeting_id))[0]

    database_url = _database_url()
    with pytest.raises(Exception, match="immutable facts"):
        async with await AsyncConnection.connect(database_url) as connection:
            await connection.execute(
                f"UPDATE {schema}.transcript_items SET text = %s WHERE id = %s",
                ("被篡改", item.id),
            )


@pytest.mark.asyncio
async def test_speaker_patch_changes_span_only_and_records_revision(repository) -> None:
    repo, meeting_id, _ = repository
    await repo.append_completed_item(meeting_id, _item())
    before = await repo.get_transcript_items(meeting_id)
    event = SpeakerPatchEvent(
        source_session_id="session-1",
        event_id="patch-1",
        sequence=1,
        stable_through_sample=320,
        patches=(
            SpeakerPatch(
                segment_uid="unit-1",
                revision=1,
                status="stable",
                source_speaker="speaker-1",
                coverage_ratio=1,
                overlap_ratio=0,
            ),
        ),
    )

    await repo.apply_speaker_patches(meeting_id, event)
    after = await repo.get_transcript_items(meeting_id)
    spans = await repo.get_transcript_attribution_spans(meeting_id)

    assert after == before
    patched = next(span for span in spans if span.source_segment_uid == "unit-1")
    assert patched.speaker_key is not None
    assert patched.speaker_revision == 1
    assert len(await repo.get_attribution_revisions(meeting_id)) == 1


@pytest.mark.asyncio
async def test_manual_override_is_mirrored_to_span_and_survives_auto_patch(repository) -> None:
    repo, meeting_id, _ = repository
    await repo.append_completed_item(meeting_id, _item())
    legacy = await repo.get_transcript(meeting_id)
    target = legacy.segments[1]

    await repo.set_speaker_override(meeting_id, target.id, "manual-speaker")
    event = SpeakerPatchEvent(
        source_session_id="session-1",
        event_id="patch-override",
        sequence=1,
        stable_through_sample=320,
        patches=(
            SpeakerPatch(
                segment_uid="unit-1",
                revision=1,
                status="stable",
                source_speaker="model-speaker",
                coverage_ratio=1,
                overlap_ratio=0,
            ),
        ),
    )
    await repo.apply_speaker_patches(meeting_id, event)

    span = next(
        span
        for span in await repo.get_transcript_attribution_spans(meeting_id)
        if span.source_segment_uid == "unit-1"
    )
    assert span.speaker_key == "manual-speaker"
    assert span.manually_corrected is True
