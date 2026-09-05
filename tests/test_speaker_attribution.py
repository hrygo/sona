"""SPK-E2E-1 speaker-only 归属事务：纯逻辑与真实 PostgreSQL 集成测试。

集成测试在专用临时 schema 上调用真实 repository（不 mock SQL）；
数据库用例被 skip 时该任务视为未通过。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection

from sona.config import MeetingSettings
from sona.meeting import migrations as _migrations_module
from sona.meeting.migrations import run_migrations
from sona.meeting.models import TranscriptReconcileResult
from sona.meeting.repository import (
    MeetingConflictError,
    PostgresMeetingRepository,
)
from sona.meeting.speaker_attribution import (
    SPEAKER_KEY_UNKNOWN,
    AttributionCandidate,
    CompletedAttributionUnit,
    CompletedItem,
    SpeakerPatch,
    SpeakerPatchEvent,
    canonical_payload_hash,
    patch_target_errors,
    resolve_patch_application,
    segment_identity,
    speaker_source_key,
)

SESSION_A = "sess_aaa"
SESSION_B = "sess_bbb"


# ---------------------------------------------------------------------------
# 纯逻辑
# ---------------------------------------------------------------------------


def _patch(
    uid: str = "u1",
    *,
    revision: int = 1,
    status: str = "stable",
    speaker: str | None = "spk_01",
) -> SpeakerPatch:
    return SpeakerPatch(
        segment_uid=uid,
        revision=revision,
        status=status,  # type: ignore[arg-type]
        source_speaker=speaker,
        coverage_ratio=0.9,
        overlap_ratio=0.0,
        candidates=(AttributionCandidate(source_speaker=speaker or "spk_01", support_ratio=0.9),)
        if speaker
        else (),
    )


def test_patch_target_errors_ok_when_contiguous() -> None:
    targets = {"u1": (0, False), "u2": (2, False)}
    event = SpeakerPatchEvent(
        source_session_id=SESSION_A,
        event_id="evt-1",
        sequence=5,
        stable_through_sample=100,
        patches=(_patch("u1", revision=1), _patch("u2", revision=3)),
    )
    assert patch_target_errors(event, targets) == []


def test_patch_target_errors_reject_unknown_uid_and_gap_and_frozen() -> None:
    targets = {"u1": (0, False), "u2": (1, True)}
    event = SpeakerPatchEvent(
        source_session_id=SESSION_A,
        event_id="evt-1",
        sequence=5,
        stable_through_sample=100,
        patches=(
            _patch("ghost", revision=1),
            _patch("u1", revision=5),
            _patch("u2", revision=2),
        ),
    )
    errors = patch_target_errors(event, targets)
    assert any(error.startswith("unknown_uid:ghost") for error in errors)
    assert any(error.startswith("revision_gap:u1:5") for error in errors)
    assert any(error.startswith("frozen:u2") for error in errors)


def test_resolve_patch_application_keeps_override() -> None:
    speaker_key, model_key, override = resolve_patch_application(
        override_key="manual-key",
        model_key="model-key",
        status="stable",
    )
    # 手动 override 后自动 patch 只更新模型证据。
    assert (speaker_key, model_key, override) == ("manual-key", "model-key", "manual-key")

    effective, model, override = resolve_patch_application(
        override_key=None, model_key="model-key", status="stable"
    )
    assert (effective, model, override) == ("model-key", "model-key", None)

    effective, model, override = resolve_patch_application(
        override_key=None, model_key=None, status="unknown"
    )
    assert (effective, model, override) == (SPEAKER_KEY_UNKNOWN, None, None)


def test_segment_identity_is_deterministic_and_scoped() -> None:
    meeting = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = segment_identity(meeting, SESSION_A, "u1")
    assert first == segment_identity(meeting, SESSION_A, "u1")
    assert first != segment_identity(meeting, SESSION_B, "u1")
    assert first != segment_identity(meeting, SESSION_A, "u2")


def test_speaker_source_key_same_session_stable_cross_session_distinct() -> None:
    meeting = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    key_a1 = speaker_source_key(meeting, SESSION_A, "spk_01")
    assert key_a1 == speaker_source_key(meeting, SESSION_A, "spk_01")
    assert key_a1 != speaker_source_key(meeting, SESSION_B, "spk_01")


def test_canonical_payload_hash_changes_with_content() -> None:
    base = {"session": "s", "updates": [1]}
    assert canonical_payload_hash(base) == canonical_payload_hash({"updates": [1], "session": "s"})
    assert canonical_payload_hash(base) != canonical_payload_hash({"session": "s", "updates": [2]})


def test_completed_item_rejects_gap_tiling() -> None:
    with pytest.raises(ValueError, match="无缝"):
        CompletedItem(
            source_session_id=SESSION_A,
            source_epoch=1,
            meeting_start_sample=0,
            item_id="item-1",
            event_id="evt-1",
            sequence=1,
            audio_start_sample=0,
            audio_end_sample=100,
            canonical_text="同意。",
            units=(
                CompletedAttributionUnit(
                    segment_uid="u1",
                    text_start=0,
                    text_end=2,
                    audio_start_sample=0,
                    audio_end_sample=50,
                    timing_quality="aligned",
                ),
            ),
        )


def test_unknown_patch_must_not_carry_speaker() -> None:
    with pytest.raises(ValueError, match="unknown"):
        _patch(status="unknown", speaker="spk_01")


# ---------------------------------------------------------------------------
# 真实 PostgreSQL 集成（专用临时 schema）
# ---------------------------------------------------------------------------


def _test_database_url() -> str:
    value = os.environ.get("SONA_TEST_DATABASE_URL")
    if not value:
        pytest.skip("SONA_TEST_DATABASE_URL 未设置；跳过真实 PostgreSQL 集成测试")
    return value


@dataclass
class SegmentSnapshot:
    """捕获后缀删除的签名：id、order、start/end、text。"""

    rows: tuple[tuple[UUID, int, int, int, str], ...]

    @classmethod
    def of(cls, document: object) -> SegmentSnapshot:
        segments = document.segments  # type: ignore[attr-defined]
        return cls(
            tuple(
                (segment.id, segment.order, segment.start_ms, segment.end_ms, segment.text)
                for segment in segments
            )
        )


class SpeakerRepoCase:
    """repo_case：在临时 schema 上调用真实 repository 的辅助外壳。"""

    def __init__(self, repository: PostgresMeetingRepository, meeting_id: UUID) -> None:
        self.repository = repository
        self.meeting_id = meeting_id

    def _completed(self, *, event_id: str, session: str = SESSION_A) -> CompletedItem:
        canonical = "甲乙丙"
        units = tuple(
            CompletedAttributionUnit(
                segment_uid=uid,
                text_start=start,
                text_end=end,
                audio_start_sample=start * 160,
                audio_end_sample=end * 160,
                timing_quality="aligned",
            )
            for uid, start, end in (("u1", 0, 1), ("u2", 1, 2), ("u3", 2, 3))
        )
        return CompletedItem(
            source_session_id=session,
            source_epoch=1,
            meeting_start_sample=0,
            item_id=f"item-{event_id}",
            event_id=event_id,
            sequence=1,
            audio_start_sample=0,
            audio_end_sample=480,
            canonical_text=canonical,
            units=units,
        )

    async def seed_three_items(self) -> SegmentSnapshot:
        result = await self.repository.append_completed_item(
            self.meeting_id, self._completed(event_id="evt-seed")
        )
        assert result is not None
        return await self.snapshot()

    def patch_middle_item(self, *, revision: int = 1) -> SpeakerPatchEvent:
        return SpeakerPatchEvent(
            source_session_id=SESSION_A,
            event_id="evt-patch-mid",
            sequence=2,
            stable_through_sample=320,
            patches=(_patch("u2", revision=revision),),
        )

    async def apply(self, event: SpeakerPatchEvent) -> object:
        return await self.repository.apply_speaker_patches(self.meeting_id, event)

    async def snapshot(self) -> SegmentSnapshot:
        return SegmentSnapshot.of(await self.repository.get_transcript(self.meeting_id))

    async def revisions(self) -> tuple[int, int]:
        meeting = await self.repository.get_meeting(self.meeting_id)
        assert meeting is not None
        return (meeting.transcript_revision, meeting.content_revision)


@pytest_asyncio.fixture
async def repo_case(tmp_path: Path) -> AsyncIterator[SpeakerRepoCase]:
    database_url = _test_database_url()
    schema = f"spk_test_{uuid4().hex[:16]}"
    async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
    settings = MeetingSettings(
        database_url=database_url,
        schema=schema,
        recovery_dir=tmp_path / "recovery",
    )
    await run_migrations(database_url, schema=schema)
    repository = PostgresMeetingRepository(settings)
    await repository.open()
    meeting = await repository.create_meeting(
        "归属测试", language="Chinese", audio_source="microphone"
    )
    try:
        yield SpeakerRepoCase(repository, meeting.id)
    finally:
        await repository.close()
        async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.asyncio
async def test_speaker_patch_never_replaces_transcript_suffix(
    repo_case: SpeakerRepoCase,
) -> None:
    before = await repo_case.seed_three_items()
    event = repo_case.patch_middle_item(revision=1)

    await repo_case.apply(event)
    once = await repo_case.snapshot()
    revisions_once = await repo_case.revisions()

    # 重复 event 不增加 revision。
    await repo_case.apply(event)
    twice = await repo_case.snapshot()
    revisions_twice = await repo_case.revisions()

    # 所有文本、时间、UUID、后续 item 必须保持。
    assert once == before
    assert twice == once
    assert revisions_twice == revisions_once


@pytest.mark.asyncio
async def test_patch_updates_middle_speaker_only(repo_case: SpeakerRepoCase) -> None:
    await repo_case.seed_three_items()
    await repo_case.apply(repo_case.patch_middle_item(revision=1))

    document = await repo_case.repository.get_transcript(repo_case.meeting_id)
    texts = [segment.text for segment in document.segments]
    assert texts == ["甲", "乙", "丙"]
    speakers = [segment.speaker_key for segment in document.segments]
    assert speakers[0] == SPEAKER_KEY_UNKNOWN
    assert speakers[1] != SPEAKER_KEY_UNKNOWN
    assert speakers[2] == SPEAKER_KEY_UNKNOWN
    assert speakers[0] != speakers[1]


@pytest.mark.asyncio
async def test_unknown_uid_fails_whole_batch(repo_case: SpeakerRepoCase) -> None:
    await repo_case.seed_three_items()
    before = await repo_case.snapshot()
    event = SpeakerPatchEvent(
        source_session_id=SESSION_A,
        event_id="evt-ghost",
        sequence=2,
        stable_through_sample=100,
        patches=(_patch("u1", revision=1), _patch("ghost", revision=1)),
    )
    with pytest.raises(MeetingConflictError):
        await repo_case.apply(event)
    assert await repo_case.snapshot() == before


@pytest.mark.asyncio
async def test_revision_skip_fails_whole_batch(repo_case: SpeakerRepoCase) -> None:
    await repo_case.seed_three_items()
    event = SpeakerPatchEvent(
        source_session_id=SESSION_A,
        event_id="evt-skip",
        sequence=2,
        stable_through_sample=100,
        patches=(_patch("u1", revision=2),),
    )
    with pytest.raises(MeetingConflictError):
        await repo_case.apply(event)


@pytest.mark.asyncio
async def test_same_event_id_different_payload_is_conflict(
    repo_case: SpeakerRepoCase,
) -> None:
    await repo_case.seed_three_items()
    event = repo_case.patch_middle_item(revision=1)
    await repo_case.apply(event)
    conflicting = event.model_copy(
        update={"patches": (_patch("u2", revision=2, speaker="spk_02"),)}
    )
    with pytest.raises(MeetingConflictError):
        await repo_case.apply(conflicting)


@pytest.mark.asyncio
async def test_duplicate_completed_item_is_idempotent(repo_case: SpeakerRepoCase) -> None:
    item = repo_case._completed(event_id="evt-seed")
    first = await repo_case.repository.append_completed_item(repo_case.meeting_id, item)
    second = await repo_case.repository.append_completed_item(repo_case.meeting_id, item)

    assert isinstance(first, TranscriptReconcileResult)
    assert second is None or second.transcript_revision == first.transcript_revision
    document = await repo_case.repository.get_transcript(repo_case.meeting_id)
    assert len(document.segments) == 3


@pytest.mark.asyncio
async def test_manual_override_survives_auto_patch(repo_case: SpeakerRepoCase) -> None:
    await repo_case.seed_three_items()
    document = await repo_case.repository.get_transcript(repo_case.meeting_id)
    middle = document.segments[1]

    manual_key = "manual-UUID-1"
    await repo_case.repository.set_speaker_override(repo_case.meeting_id, middle.id, manual_key)
    await repo_case.apply(repo_case.patch_middle_item(revision=1))

    after = await repo_case.repository.get_transcript(repo_case.meeting_id)
    patched = next(segment for segment in after.segments if segment.id == middle.id)
    assert patched.speaker_key == manual_key

    # 撤销恢复之前 key（模型值）。
    await repo_case.repository.clear_speaker_override(repo_case.meeting_id, middle.id)
    restored = await repo_case.repository.get_transcript(repo_case.meeting_id)
    restored_segment = next(segment for segment in restored.segments if segment.id == middle.id)
    assert restored_segment.speaker_key != manual_key


@pytest.mark.asyncio
async def test_unknown_session_fails_whole_batch(repo_case: SpeakerRepoCase) -> None:
    """session 未注册（无 completed）时 patch 整批拒绝。"""
    await repo_case.seed_three_items()
    event = SpeakerPatchEvent(
        source_session_id=SESSION_B,
        event_id="evt-b1",
        sequence=1,
        stable_through_sample=10,
        patches=(),
    )
    with pytest.raises(MeetingConflictError):
        await repo_case.apply(event)


@pytest.mark.asyncio
async def test_migration_from_legacy_schema_upgrade(tmp_path: Path) -> None:
    """旧 schema（0001+0002）可以直接升级；旧必填列与旧查询保持可读。"""
    database_url = _test_database_url()
    schema = f"spk_legacy_{uuid4().hex[:16]}"
    migrations_dir = Path(_migrations_module.__file__).parent / "migrations"
    assert migrations_dir.is_dir(), "migrations 目录缺失"
    async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        for name in ("0001_initial.sql", "0002_inner_os_exchanges.sql"):
            sql = (migrations_dir / name).read_text(encoding="utf-8").replace(
                "__SCHEMA__", f'"{schema}"'
            )
            await admin.execute(sql)
    await run_migrations(database_url, schema=schema)

    async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
        cursor = await admin.execute(
            f"""SELECT column_name FROM information_schema.columns
                WHERE table_schema = '{schema}' AND table_name = 'transcript_segments'
                AND column_name IN ('speaker_status', 'model_speaker_key',
                                    'speaker_override_key', 'source_segment_uid')"""
        )
        assert len(await cursor.fetchall()) == 4
        cursor = await admin.execute(
            f"""SELECT table_name FROM information_schema.tables
                WHERE table_schema = '{schema}'
                AND table_name IN ('meeting_transcription_sources',
                                   'meeting_speaker_sources', 'meeting_source_events')"""
        )
        assert len(await cursor.fetchall()) == 3
        # 旧查询路径保持可读：旧行 speaker_status 默认 unknown。
        cursor = await admin.execute(
            f"SELECT count(*) FROM {schema}.transcript_segments WHERE speaker_status = 'unknown'"
        )
        row = await cursor.fetchone()
        assert row is not None and int(row[0]) == 0
    async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
