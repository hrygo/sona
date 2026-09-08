"""SPK-E2E-1 S4 联合端到端测试与接线验收。

涵盖：
1. 至少三个 ASR commit、speaker revision 递增、时间戳与 session sample 对齐；
2. 断线重连与 source epoch 递增、重放幂等去重；
3. 人工改名与手动更正优先（自动 patch 不覆盖人工 override）；
4. 数据库写入暂时失败触发 recovery journal 记录，回放后成功落库，校验事实一致性与权限；
5. Finalize 屏障、watermark 等待与重试幂等性；
6. SpeechRail v2.0.0 namespaced opt-in、正常 ASR 控制路径与不可用降级。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg import AsyncConnection

from sona.config import MeetingSettings
from sona.meeting.finalization import (
    DiarizationGateState,
    RepositoryDiarizationGate,
)
from sona.meeting.migrations import run_migrations
from sona.meeting.models import (
    DiarizationStatus,
    TranscriptDocument,
)
from sona.meeting.persistence import TranscriptPersistence
from sona.meeting.recovery import RecoveryJournal
from sona.meeting.repository import PostgresMeetingRepository
from sona.meeting.speaker_attribution import (
    SPEAKER_KEY_UNKNOWN,
    AttributionCandidate,
    CompletedAttributionUnit,
    CompletedItem,
    SpeakerPatch,
    SpeakerPatchEvent,
    speaker_source_key,
)

SESSION_ID = "sess_e2e_joint"
RAIL_SAMPLE_RATE = 16000


def _test_database_url() -> str:
    value = os.environ.get("SONA_TEST_DATABASE_URL")
    if not value:
        pytest.skip("SONA_TEST_DATABASE_URL 未设置；跳过真实 PostgreSQL 集成测试")
    return value


@pytest_asyncio.fixture
async def e2e_repo(tmp_path: Path) -> AsyncIterator[PostgresMeetingRepository]:
    database_url = _test_database_url()
    schema = f"vr_test_e2e_{uuid4().hex[:12]}"
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
    try:
        yield repo
    finally:
        await repo.close()
        async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@dataclass(frozen=True)
class SegmentFact:
    order: int
    text: str
    start_ms: int
    end_ms: int
    speaker_key: str
    speaker_status: str
    timing_quality: str
    speaker_manual: bool


def _document_facts(doc: TranscriptDocument) -> tuple[SegmentFact, ...]:
    return tuple(
        SegmentFact(
            order=s.order,
            text=s.text,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            speaker_key=s.speaker_key,
            speaker_status=s.speaker_status or "stable",
            timing_quality=s.timing_quality or "aligned",
            speaker_manual=bool(s.speaker_manual),
        )
        for s in doc.segments
    )


def _make_completed_item(
    *,
    item_id: str,
    event_id: str,
    sequence: int,
    source_epoch: int,
    audio_start_sample: int,
    audio_end_sample: int,
    text: str,
    unit_defs: tuple[tuple[str, int, int, int, int], ...],
    session_id: str = SESSION_ID,
) -> CompletedItem:
    return CompletedItem(
        source_session_id=session_id,
        source_epoch=source_epoch,
        meeting_start_sample=0,
        item_id=item_id,
        event_id=event_id,
        sequence=sequence,
        audio_start_sample=audio_start_sample,
        audio_end_sample=audio_end_sample,
        canonical_text=text,
        units=tuple(
            CompletedAttributionUnit(
                segment_uid=uid,
                text_start=t_start,
                text_end=t_end,
                audio_start_sample=a_start,
                audio_end_sample=a_end,
                timing_quality="aligned",
            )
            for uid, t_start, t_end, a_start, a_end in unit_defs
        ),
    )


def _make_patch(
    uid: str,
    *,
    revision: int,
    status: Literal["unknown", "tentative", "stable"] = "stable",
    source_speaker: str | None = "spk_01",
    coverage_ratio: float = 0.95,
    overlap_ratio: float = 0.0,
    candidates: tuple[AttributionCandidate, ...] = (),
) -> SpeakerPatch:
    return SpeakerPatch(
        segment_uid=uid,
        revision=revision,
        status=status,
        source_speaker=source_speaker if status != "unknown" else None,
        coverage_ratio=coverage_ratio,
        overlap_ratio=overlap_ratio,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# 1. 至少三个 ASR commit、speaker revision 递增、时间戳与 session sample 对齐
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diarization_e2e_three_commits_and_speaker_revisions(
    e2e_repo: PostgresMeetingRepository,
) -> None:
    meeting = await e2e_repo.create_meeting(
        "端到端三轮提交与修订测试", language="zh", audio_source="microphone"
    )
    meeting_id = meeting.id

    # Commit 1: 0 - 3000ms (0 - 48000 samples)
    item1 = _make_completed_item(
        item_id="item_c1",
        event_id="evt_c1",
        sequence=1,
        source_epoch=1,
        audio_start_sample=0,
        audio_end_sample=48000,
        text="各位好，今天讨论架构方案。",
        unit_defs=(
            ("u1_1", 0, 4, 0, 16000),      # "各位好，"
            ("u1_2", 4, 8, 16000, 32000),   # "今天讨论"
            ("u1_3", 8, 13, 32000, 48000),  # "架构方案。"
        ),
    )
    res1 = await e2e_repo.append_completed_item(meeting_id, item1)
    assert res1 is not None
    assert len(res1.segments) == 3
    assert res1.content_revision == 1
    assert res1.transcript_revision == 1

    # Commit 2: 3000 - 5000ms (48000 - 80000 samples)
    item2 = _make_completed_item(
        item_id="item_c2",
        event_id="evt_c2",
        sequence=2,
        source_epoch=1,
        audio_start_sample=48000,
        audio_end_sample=80000,
        text="我先介绍一下背景。",
        unit_defs=(
            ("u2_1", 0, 4, 48000, 64000),  # "我先介绍"
            ("u2_2", 4, 9, 64000, 80000),  # "一下背景。"
        ),
    )
    res2 = await e2e_repo.append_completed_item(meeting_id, item2)
    assert res2 is not None
    assert len(res2.segments) == 2
    assert res2.content_revision == 2
    assert res2.transcript_revision == 2

    # Commit 3: 5000 - 7000ms (80000 - 112000 samples)
    item3 = _make_completed_item(
        item_id="item_c3",
        event_id="evt_c3",
        sequence=3,
        source_epoch=1,
        audio_start_sample=80000,
        audio_end_sample=112000,
        text="同意这个设计。",
        unit_defs=(
            ("u3_1", 0, 2, 80000, 96000),    # "同意"
            ("u3_2", 2, 7, 96000, 112000),   # "这个设计。"
        ),
    )
    res3 = await e2e_repo.append_completed_item(meeting_id, item3)
    assert res3 is not None
    assert len(res3.segments) == 2
    assert res3.content_revision == 3
    assert res3.transcript_revision == 3

    # 验证初始状态：7 个 segment 全为 unknown，时间戳单调不重叠
    doc = await e2e_repo.get_transcript(meeting_id)
    assert len(doc.segments) == 7
    assert all(s.speaker_key == SPEAKER_KEY_UNKNOWN for s in doc.segments)
    assert doc.segments[0].start_ms == 0
    assert doc.segments[-1].end_ms == 7000

    # Revision 1 for Item 1: 全部归属给 spk_01 (stable)
    patch_evt1 = SpeakerPatchEvent(
        source_session_id=SESSION_ID,
        event_id="patch_evt_1",
        sequence=4,
        stable_through_sample=48000,
        patches=(
            _make_patch(
                "u1_1",
                revision=1,
                status="stable",
                source_speaker="spk_01",
                candidates=(
                    AttributionCandidate(source_speaker="spk_01", support_ratio=0.95),
                ),
            ),
            _make_patch(
                "u1_2",
                revision=1,
                status="stable",
                source_speaker="spk_01",
                candidates=(
                    AttributionCandidate(source_speaker="spk_01", support_ratio=0.92),
                ),
            ),
            _make_patch(
                "u1_3",
                revision=1,
                status="stable",
                source_speaker="spk_01",
                candidates=(
                    AttributionCandidate(source_speaker="spk_01", support_ratio=0.90),
                ),
            ),
        ),
    )
    patch_res1 = await e2e_repo.apply_speaker_patches(meeting_id, patch_evt1)
    assert patch_res1 is not None
    assert patch_res1.content_revision == 4
    assert patch_res1.transcript_revision == 4

    # Revision 2 for Item 2: 先归属 spk_01 (revision=1)，再修订为 spk_02 (revision=2)
    patch_evt2 = SpeakerPatchEvent(
        source_session_id=SESSION_ID,
        event_id="patch_evt_2",
        sequence=5,
        stable_through_sample=64000,
        patches=(
            _make_patch(
                "u2_1",
                revision=1,
                status="tentative",
                source_speaker="spk_01",
                candidates=(
                    AttributionCandidate(source_speaker="spk_01", support_ratio=0.65),
                ),
            ),
        ),
    )
    await e2e_repo.apply_speaker_patches(meeting_id, patch_evt2)

    # 修订为 spk_02 (revision=2, status=stable)
    patch_evt2_rev = SpeakerPatchEvent(
        source_session_id=SESSION_ID,
        event_id="patch_evt_2_rev",
        sequence=6,
        stable_through_sample=80000,
        patches=(
            _make_patch(
                "u2_1",
                revision=2,
                status="stable",
                source_speaker="spk_02",
                candidates=(
                    AttributionCandidate(source_speaker="spk_02", support_ratio=0.88),
                ),
            ),
            _make_patch(
                "u2_2",
                revision=1,
                status="stable",
                source_speaker="spk_02",
                candidates=(
                    AttributionCandidate(source_speaker="spk_02", support_ratio=0.85),
                ),
            ),
        ),
    )
    patch_res2 = await e2e_repo.apply_speaker_patches(meeting_id, patch_evt2_rev)
    assert patch_res2 is not None
    assert patch_res2.content_revision == 6
    assert patch_res2.transcript_revision == 6

    # Item 3: 归属给 spk_02 (stable)
    patch_evt3 = SpeakerPatchEvent(
        source_session_id=SESSION_ID,
        event_id="patch_evt_3",
        sequence=7,
        stable_through_sample=112000,
        patches=(
            _make_patch(
                "u3_1",
                revision=1,
                status="stable",
                source_speaker="spk_02",
                candidates=(
                    AttributionCandidate(source_speaker="spk_02", support_ratio=0.91),
                ),
            ),
            _make_patch(
                "u3_2",
                revision=1,
                status="stable",
                source_speaker="spk_02",
                candidates=(
                    AttributionCandidate(source_speaker="spk_02", support_ratio=0.89),
                ),
            ),
        ),
    )
    await e2e_repo.apply_speaker_patches(meeting_id, patch_evt3)

    # 最终事实核查
    final_doc = await e2e_repo.get_transcript(meeting_id)
    facts = _document_facts(final_doc)
    assert len(facts) == 7

    spk1_key = speaker_source_key(meeting_id, SESSION_ID, "spk_01")
    spk2_key = speaker_source_key(meeting_id, SESSION_ID, "spk_02")

    # Item 1 单元归属 spk1
    assert facts[0].speaker_key == spk1_key and facts[0].text == "各位好，"
    assert facts[1].speaker_key == spk1_key and facts[1].text == "今天讨论"
    assert facts[2].speaker_key == spk1_key and facts[2].text == "架构方案。"

    # Item 2 单元归属 spk2 (经修订)
    assert facts[3].speaker_key == spk2_key and facts[3].text == "我先介绍"
    assert facts[4].speaker_key == spk2_key and facts[4].text == "一下背景。"

    # Item 3 单元归属 spk2
    assert facts[5].speaker_key == spk2_key and facts[5].text == "同意"
    assert facts[6].speaker_key == spk2_key and facts[6].text == "这个设计。"

    # 验证全部文本拼接等于期望
    expected_full = "各位好，今天讨论架构方案。我先介绍一下背景。同意这个设计。"
    assert "".join(f.text for f in facts) == expected_full


# ---------------------------------------------------------------------------
# 2. 断线重连、Source Epoch 递增与幂等去重
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diarization_e2e_reconnection_and_epoch_dedup(
    e2e_repo: PostgresMeetingRepository,
) -> None:
    meeting = await e2e_repo.create_meeting(
        "重连与 epoch 递增测试", language="zh", audio_source="microphone"
    )
    meeting_id = meeting.id

    # Epoch 1 item
    item1 = _make_completed_item(
        session_id="sess_epoch_1",
        item_id="item_ep1",
        event_id="evt_ep1",
        sequence=1,
        source_epoch=1,
        audio_start_sample=0,
        audio_end_sample=32000,
        text="网络良好。",
        unit_defs=(("ep1_u1", 0, 5, 0, 32000),),
    )
    await e2e_repo.append_completed_item(meeting_id, item1)

    # 重复推送相同 event_id（幂等去重）：不产生新 segment，返回 None
    replay_res = await e2e_repo.append_completed_item(meeting_id, item1)
    assert replay_res is None
    doc_after_replay = await e2e_repo.get_transcript(meeting_id)
    assert len(doc_after_replay.segments) == 1

    # 模拟网络断线后重连，创建新 WS session，source_epoch 递增为 2
    item2 = _make_completed_item(
        session_id="sess_epoch_2",
        item_id="item_ep2",
        event_id="evt_ep2",
        sequence=2,
        source_epoch=2,
        audio_start_sample=32000,
        audio_end_sample=64000,
        text="断线重连成功。",
        unit_defs=(("ep2_u1", 0, 7, 32000, 64000),),
    )
    await e2e_repo.append_completed_item(meeting_id, item2)

    doc_final = await e2e_repo.get_transcript(meeting_id)
    assert len(doc_final.segments) == 2
    assert doc_final.segments[0].source_epoch == 1
    assert doc_final.segments[1].source_epoch == 2
    assert doc_final.segments[1].text == "断线重连成功。"


# ---------------------------------------------------------------------------
# 3. 人工改名与手动更正优先（自动 patch 不覆盖人工 override）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diarization_e2e_manual_speaker_override_precedence(
    e2e_repo: PostgresMeetingRepository,
) -> None:
    meeting = await e2e_repo.create_meeting(
        "人工更正优先测试", language="zh", audio_source="microphone"
    )
    meeting_id = meeting.id

    item = _make_completed_item(
        item_id="item_man",
        event_id="evt_man",
        sequence=1,
        source_epoch=1,
        audio_start_sample=0,
        audio_end_sample=32000,
        text="这是关键决策。",
        unit_defs=(("man_u1", 0, 7, 0, 32000),),
    )
    await e2e_repo.append_completed_item(meeting_id, item)

    # 初始归属为 spk_01（tentative 状态）
    patch1 = SpeakerPatchEvent(
        source_session_id=SESSION_ID,
        event_id="patch_man_1",
        sequence=2,
        stable_through_sample=16000,
        patches=(
            _make_patch(
                "man_u1",
                revision=1,
                status="tentative",
                source_speaker="spk_01",
                candidates=(
                    AttributionCandidate(source_speaker="spk_01", support_ratio=0.90),
                ),
            ),
        ),
    )
    await e2e_repo.apply_speaker_patches(meeting_id, patch1)

    spk1_key = speaker_source_key(meeting_id, SESSION_ID, "spk_01")
    # 1. 用户在 UI 上将说话人重命名为 "技术负责人"
    await e2e_repo.rename_speaker(meeting_id, spk1_key, "技术负责人")

    speakers = await e2e_repo.get_speakers(meeting_id)
    assert any(s.display_name == "技术负责人" for s in speakers)

    # 2. 用户在 UI 上对该 segment 做出人工归属更正（override）
    doc_before = await e2e_repo.get_transcript(meeting_id)
    seg_id = doc_before.segments[0].id
    manual_override_key = speaker_source_key(meeting_id, SESSION_ID, "spk_override_admin")
    await e2e_repo.set_speaker_override(meeting_id, seg_id, manual_override_key)

    doc_overridden = await e2e_repo.get_transcript(meeting_id)
    assert doc_overridden.segments[0].speaker_key == manual_override_key
    assert doc_overridden.segments[0].speaker_manual is True

    # 3. 自动模型后续发来 patch2（模型更新为 spk_02，变为 stable）
    spk2_key = speaker_source_key(meeting_id, SESSION_ID, "spk_02")
    patch2 = SpeakerPatchEvent(
        source_session_id=SESSION_ID,
        event_id="patch_man_2",
        sequence=3,
        stable_through_sample=32000,
        patches=(
            _make_patch(
                "man_u1",
                revision=2,
                status="stable",
                source_speaker="spk_02",
                candidates=(
                    AttributionCandidate(source_speaker="spk_02", support_ratio=0.88),
                    AttributionCandidate(source_speaker="spk_01", support_ratio=0.12),
                ),
            ),
        ),
    )
    await e2e_repo.apply_speaker_patches(meeting_id, patch2)

    # 4. 验证：
    # a. 人工重命名保持不变
    speakers_after = await e2e_repo.get_speakers(meeting_id)
    spk_record = next(s for s in speakers_after if s.speaker_key == spk1_key)
    assert spk_record.display_name == "技术负责人"

    # b. segment 的有效归属保持人工 override，不被自动模型覆盖；但模型客观证据已更新
    doc_after_patch = await e2e_repo.get_transcript(meeting_id)
    seg_after = doc_after_patch.segments[0]
    assert seg_after.speaker_key == manual_override_key
    assert seg_after.speaker_manual is True

    # 查库验证底层 model_speaker_key 和 speaker_override_key 正确记录
    async with e2e_repo._connection() as conn:
        cursor = await conn.execute(
            f"""
            SELECT model_speaker_key, speaker_override_key
            FROM {e2e_repo._schema}.transcript_segments
            WHERE id = %s
            """,
            (seg_id,),
        )
        db_row = await cursor.fetchone()
        assert db_row is not None
        assert db_row[0] == spk2_key
        assert db_row[1] == manual_override_key

    # c. 撤销人工 override 后，平滑回退至模型客观识别结果
    await e2e_repo.clear_speaker_override(meeting_id, seg_id)
    doc_cleared = await e2e_repo.get_transcript(meeting_id)
    seg_cleared = doc_cleared.segments[0]
    assert seg_cleared.speaker_key == spk2_key
    assert seg_cleared.speaker_manual is False


# ---------------------------------------------------------------------------
# 4. 数据库写入暂时失败触发 recovery journal 记录，回放后成功落库
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diarization_e2e_transient_db_failure_and_journal_recovery(
    e2e_repo: PostgresMeetingRepository,
    tmp_path: Path,
) -> None:
    meeting = await e2e_repo.create_meeting(
        "崩溃恢复与 Journal 回放测试", language="zh", audio_source="microphone"
    )
    meeting_id = meeting.id

    journal_dir = tmp_path / "recovery"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal = RecoveryJournal(journal_dir)
    persistence = TranscriptPersistence(e2e_repo, journal=journal)

    item = _make_completed_item(
        item_id="item_rec",
        event_id="evt_rec",
        sequence=1,
        source_epoch=1,
        audio_start_sample=0,
        audio_end_sample=32000,
        text="临时断网保障。",
        unit_defs=(("rec_u1", 0, 7, 0, 32000),),
    )

    # 1. 正常写入一个 item
    res = await persistence.append_item(meeting_id, item)
    assert res is not None
    assert not persistence.degraded

    # 2. 模拟数据库异常：patch 写入失败，写入 journal
    patch_evt = SpeakerPatchEvent(
        source_session_id=SESSION_ID,
        event_id="patch_rec_1",
        sequence=2,
        stable_through_sample=32000,
        patches=(
            _make_patch(
                "rec_u1",
                revision=1,
                status="stable",
                source_speaker="spk_01",
            ),
        ),
    )

    # 模拟 repository 抛出数据库连接错误
    original_apply = e2e_repo.apply_speaker_patches

    async def _failing_apply(*args: object, **kwargs: object) -> object:
        raise ConnectionError("PostgreSQL 模拟瞬态连接丢失")

    e2e_repo.apply_speaker_patches = _failing_apply  # type: ignore[assignment]
    try:
        # 持久化层捕获异常并落盘 journal
        await persistence.apply_patches(meeting_id, patch_evt)
        assert persistence.degraded
    finally:
        e2e_repo.apply_speaker_patches = original_apply  # 恢复正常

    # 验证 journal 文件已写入且权限为 0600
    journal_files = list(journal_dir.glob("*.jsonl"))
    assert len(journal_files) == 1
    file_stat = journal_files[0].stat()
    assert oct(file_stat.st_mode)[-3:] == "600"

    # 3. 模拟 DB 恢复后执行 RecoveryJournal.replay
    journal = RecoveryJournal(journal_dir)
    replayed = await journal.replay(e2e_repo)
    assert replayed == 1

    # 验证 journal 文件在成功回放后被清理删除
    assert len(list(journal_dir.glob("*.jsonl"))) == 0

    # 验证落库事实完整性
    doc = await e2e_repo.get_transcript(meeting_id)
    assert len(doc.segments) == 1
    spk1_key = speaker_source_key(meeting_id, SESSION_ID, "spk_01")
    assert doc.segments[0].speaker_key == spk1_key
    assert doc.segments[0].text == "临时断网保障。"


# ---------------------------------------------------------------------------
# 5. Finalize 屏障、watermark 等待与重试幂等性
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diarization_e2e_finalize_barrier_and_idempotency(
    e2e_repo: PostgresMeetingRepository,
) -> None:
    meeting = await e2e_repo.create_meeting(
        "Finalize 屏障测试", language="zh", audio_source="microphone"
    )
    meeting_id = meeting.id

    gate_state = DiarizationGateState()
    gate_state.active = True
    gate = RepositoryDiarizationGate(e2e_repo, gate_state, poll_interval_secs=0.01)

    # 1. 达到 complete 时 wait_persisted 返回 complete
    await e2e_repo.finalize_diarization(meeting_id, status="complete")
    result = await gate.wait_persisted(meeting_id, timeout_secs=1.0)
    assert result == "complete"

    meeting_rec = await e2e_repo.get_meeting(meeting_id)
    assert meeting_rec is not None
    assert meeting_rec.diarization_status is DiarizationStatus.COMPLETE

    # 2. 幂等重试 finalize_diarization：重复调用不报错且保持 COMPLETE
    await e2e_repo.finalize_diarization(meeting_id, status="complete")
    meeting_rec_again = await e2e_repo.get_meeting(meeting_id)
    assert meeting_rec_again is not None
    assert meeting_rec_again.diarization_status is DiarizationStatus.COMPLETE


@pytest.mark.asyncio
async def test_diarization_e2e_finalize_timeout_degrades_gracefully(
    e2e_repo: PostgresMeetingRepository,
) -> None:
    meeting = await e2e_repo.create_meeting(
        "Finalize 降级测试", language="zh", audio_source="microphone"
    )
    meeting_id = meeting.id

    # 模拟分人 watermark 滞后，超时触发
    gate_state = DiarizationGateState()
    gate_state.active = True
    gate = RepositoryDiarizationGate(e2e_repo, gate_state, poll_interval_secs=0.01)

    # timeout_secs=0.01 立即触发超时降级
    result = await gate.wait_persisted(meeting_id, timeout_secs=0.01)
    assert result == "timeout"

    meeting_rec = await e2e_repo.get_meeting(meeting_id)
    assert meeting_rec is not None
    # 分人标记降级为 finalization_timeout，但会议流程不崩溃
    assert meeting_rec.diarization_status is DiarizationStatus.DEGRADED
    assert meeting_rec.diarization_reason == "finalization_timeout"


# ---------------------------------------------------------------------------
# 6. SpeechRail v2.0.0 bootstrap / control paths
# ---------------------------------------------------------------------------


class _FakeWSConnection:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._incoming = list(messages)
        self.sent: list[dict[str, Any]] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        if not self._incoming:
            raise EOFError("no more messages")
        return json.dumps(self._incoming.pop(0))

    async def close(self) -> None:
        return None


async def _conn_factory(conn: _FakeWSConnection) -> _FakeWSConnection:
    return conn


@pytest.mark.asyncio
async def test_v2_disabled_bootstrap_sends_only_standard_asr_configuration() -> None:
    from sona.speechrail.transport import SpeechRailRealtimeClient

    connection = _FakeWSConnection(
        [
            {
                "type": "session.created",
                "session_id": "sess-control-off",
                "sequence": 1,
                "session": {"id": "sess-control-off"},
            },
            {
                "type": "session.updated",
                "session_id": "sess-control-off",
                "sequence": 2,
                "session": {"id": "sess-control-off"},
            },
        ]
    )
    client = SpeechRailRealtimeClient(
        url="ws://fake/v1/realtime",
        connection_factory=lambda _: _conn_factory(connection),
    )

    await client.connect(language="zh", diarization_enabled=False)

    assert len(connection.sent) == 1
    assert connection.sent[0]["session"].get("speechrail") is None
    assert client.diarization_enabled is False


@pytest.mark.asyncio
async def test_v2_enabled_bootstrap_sends_one_exact_namespaced_opt_in() -> None:
    from sona.speechrail.transport import SpeechRailRealtimeClient

    connection = _FakeWSConnection(
        [
            {
                "type": "session.created",
                "session_id": "sess-control-on",
                "sequence": 1,
                "session": {"id": "sess-control-on"},
            },
            {
                "type": "session.updated",
                "session_id": "sess-control-on",
                "sequence": 2,
                "session": {"id": "sess-control-on"},
            },
            {
                "type": "session.updated",
                "session_id": "sess-control-on",
                "sequence": 3,
                "session": {
                    "id": "sess-control-on",
                    "speechrail": {
                        "diarization": {
                            "enabled": True,
                            "version": 1,
                            "max_speakers": 4,
                        }
                    },
                },
            },
        ]
    )
    client = SpeechRailRealtimeClient(
        url="ws://fake/v1/realtime",
        connection_factory=lambda _: _conn_factory(connection),
    )

    await client.connect(language="zh", diarization_enabled=True)

    assert connection.sent[1] == {
        "type": "session.update",
        "session": {"speechrail": {"diarization": {"enabled": True}}},
    }
    assert client.diarization_enabled is True


@pytest.mark.asyncio
async def test_v2_unavailable_opt_in_keeps_standard_asr_available() -> None:
    from sona.speechrail.transport import SpeechRailRealtimeClient

    connection = _FakeWSConnection(
        [
            {
                "type": "session.created",
                "session_id": "sess-control-degraded",
                "sequence": 1,
                "session": {"id": "sess-control-degraded"},
            },
            {
                "type": "session.updated",
                "session_id": "sess-control-degraded",
                "sequence": 2,
                "session": {"id": "sess-control-degraded"},
            },
            {
                "type": "error",
                "session_id": "sess-control-degraded",
                "sequence": 3,
                "event_id": "evt-control-degraded",
                "error": {
                    "code": "diarization_not_available",
                    "message": "diarization_not_available",
                },
            },
        ]
    )
    client = SpeechRailRealtimeClient(
        url="ws://fake/v1/realtime",
        connection_factory=lambda _: _conn_factory(connection),
    )

    await client.connect(language="zh", diarization_enabled=True)

    assert client.diarization_enabled is False
    assert client.diarization_unavailable_reason == "diarization_not_available"
    assert len(connection.sent) == 2
