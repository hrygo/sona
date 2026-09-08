"""会议助手 PostgreSQL repository。

所有写入都在显式事务中完成，并且 schema 名称只通过启动时的严格校验后
插入 SQL 标识符位置；用户可控文本始终作为参数传给 psycopg。
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from sona.config import MeetingSettings
from sona.meeting.minutes_rendering import render_minutes_markdown

from .migrations import validate_schema_name
from .models import (
    DiarizationStatus,
    MeetingPage,
    MeetingRecord,
    MeetingStatus,
    MinutesJob,
    MinutesRecord,
    MinutesResult,
    MinutesStatus,
    NormalizedSegment,
    SpeakerRecord,
    TranscriptDocument,
    TranscriptReconcileResult,
    TranscriptWindow,
)
from .ports import MeetingRepository as MeetingRepository
from .speaker_attribution import (
    SPEAKER_KEY_UNKNOWN,
    CompletedItem,
    SpeakerPatchEvent,
    SpeakerPatchResult,
    canonical_payload_hash,
    patch_target_errors,
    resolve_patch_application,
    segment_identity,
    speaker_source_key,
)
from .speaker_labels import speaker_display_label

_MEETING_COLUMNS = """
    id, title, status, language, audio_source, started_at, ended_at,
    transcript_revision, content_revision, interruption_reason,
    diarization_status, diarization_reason, metadata,
    created_at, updated_at
"""
_MINUTES_COLUMNS = """
    id, meeting_id, version, status, source_content_revision, model,
    prompt_version, content_json, content_markdown, raw_output, error_code,
    error_message, lease_until, attempts, created_at, generated_at, updated_at
"""
_MINUTES_COLUMNS_QUALIFIED = """
    minutes.id, minutes.meeting_id, minutes.version, minutes.status,
    minutes.source_content_revision, minutes.model, minutes.prompt_version,
    minutes.content_json, minutes.content_markdown, minutes.raw_output,
    minutes.error_code, minutes.error_message, minutes.lease_until,
    minutes.attempts, minutes.created_at, minutes.generated_at, minutes.updated_at
"""
# failed 任务自动重试的冷却期，避免模型持续不可用时紧密循环重试
_FAILED_RETRY_COOLDOWN_SECS = 30
_SEGMENT_COLUMNS = """
    id, segment_order, source_epoch, speaker_key, start_ms, end_ms, text,
    translation, detected_language, created_at, updated_at
"""

# SPK-E2E-1 归属证据列（get_transcript 专用；reconcile 签名沿用 _SEGMENT_COLUMNS）。
_SEGMENT_COLUMNS_DETAIL = """
    id, segment_order, source_epoch, speaker_key, start_ms, end_ms, text,
    translation, detected_language, speaker_status, timing_quality,
    overlap_ratio, (speaker_override_key IS NOT NULL) AS speaker_manual
"""

logger = logging.getLogger(__name__)


class MeetingRepositoryError(RuntimeError):
    """repository 层的稳定错误基类。"""


class MeetingNotFoundError(MeetingRepositoryError):
    """请求的会议或纪要不存在。"""


class MeetingConflictError(MeetingRepositoryError):
    """当前资源状态不允许执行请求。"""


class RepositoryUnavailableError(MeetingRepositoryError):
    """数据库暂不可用。"""


class InvalidCursorError(MeetingRepositoryError):
    """游标不是 repository 产生的有效值。"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_bounded_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= 200:
        raise ValueError(f"{label}长度必须为 1–200")
    if any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{label}不能包含控制字符")
    return normalized


def _validate_title(value: str) -> str:
    return _validate_bounded_text(value, label="会议标题")


def _validate_display_name(value: str) -> str:
    return _validate_bounded_text(value, label="说话人名称")


def _encode_cursor(created_at: datetime, meeting_id: UUID) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(meeting_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        meeting_id = UUID(str(payload["id"]))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("会议游标无效") from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at, meeting_id


def _meeting_from_row(row: Sequence[Any]) -> MeetingRecord:
    return MeetingRecord(
        id=cast(UUID, row[0]),
        title=str(row[1]),
        status=MeetingStatus(str(row[2])),
        language=str(row[3]),
        audio_source=str(row[4]),
        started_at=cast(datetime, row[5]),
        ended_at=cast(datetime | None, row[6]),
        transcript_revision=int(row[7]),
        content_revision=int(row[8]),
        interruption_reason=cast(str | None, row[9]),
        diarization_status=DiarizationStatus(str(row[10])),
        diarization_reason=cast(str | None, row[11]),
        metadata=dict(cast(Mapping[str, Any], row[12] or {})),
        created_at=cast(datetime, row[13]),
        updated_at=cast(datetime, row[14]),
    )


def _minutes_from_row(row: Sequence[Any]) -> MinutesRecord:
    content = row[7]
    parsed_content = MinutesResult.model_validate(content) if content is not None else None
    return MinutesRecord(
        id=cast(UUID, row[0]),
        meeting_id=cast(UUID, row[1]),
        version=int(row[2]),
        status=MinutesStatus(str(row[3])),
        source_content_revision=int(row[4]),
        model=str(row[5]),
        prompt_version=str(row[6]),
        content_json=parsed_content,
        content_markdown=cast(str | None, row[8]),
        raw_output=cast(str | None, row[9]),
        error_code=cast(str | None, row[10]),
        error_message=cast(str | None, row[11]),
        lease_until=cast(datetime | None, row[12]),
        attempts=int(row[13]),
        created_at=cast(datetime, row[14]),
        generated_at=cast(datetime | None, row[15]),
        updated_at=cast(datetime, row[16]),
    )


class PostgresMeetingRepository:
    """使用 psycopg 3 异步连接池的会议 repository。"""

    def __init__(
        self,
        settings: MeetingSettings,
        *,
        pool: AsyncConnectionPool[Any] | None = None,
    ) -> None:
        self.settings = settings
        self.schema = validate_schema_name(settings.schema_name)
        self._schema = f'"{self.schema}"'
        self._pool: AsyncConnectionPool[Any] = pool or AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=max(2, settings.summary_concurrency + 1),
            open=False,
            kwargs={"row_factory": tuple_row},
        )
        self._owns_pool = pool is None
        self._opened = False

    async def open(self) -> None:
        """打开连接池；多次调用安全。"""
        if not self._opened:
            await self._pool.open(wait=True)
            self._opened = True

    async def start(self) -> None:
        """生命周期别名，供应用启动器使用。"""
        await self.open()

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        await self.open()
        async with self._pool.connection() as connection:
            yield connection

    async def check_writable(self) -> bool:
        try:
            async with self._connection() as connection:
                cursor = await connection.execute("SELECT 1")
                await cursor.fetchone()
                return True
        except Exception as exc:
            logger.warning("PostgresMeetingRepository: 数据库不可写检查失败: %s", exc)
            return False

    async def create_meeting(
        self, title: str, *, language: str, audio_source: str
    ) -> MeetingRecord:
        title = _validate_title(title)
        language = language.strip()
        audio_source = audio_source.strip()
        if not language or len(language) > 32:
            raise ValueError("会议语言无效")
        if audio_source != "microphone":
            raise ValueError("首版会议只支持 microphone 音频源")
        meeting_id = uuid4()
        now = _utc_now()
        async with self._connection() as connection:  # noqa: SIM117
            async with connection.transaction():
                await connection.execute(
                    f"""
                    INSERT INTO {self._schema}.meetings
                        (id, title, status, language, audio_source, started_at,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        meeting_id,
                        title,
                        MeetingStatus.RECORDING.value,
                        language,
                        audio_source,
                        now,
                        now,
                        now,
                    ),
                )
                await self._insert_event(connection, meeting_id, "meeting_started", {})
                cursor = await connection.execute(
                    f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings WHERE id = %s",
                    (meeting_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RepositoryUnavailableError("会议创建后无法读取")
                return _meeting_from_row(row)

    async def get_meeting(self, meeting_id: UUID) -> MeetingRecord | None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings WHERE id = %s",
                (meeting_id,),
            )
            row = await cursor.fetchone()
            return _meeting_from_row(row) if row is not None else None

    async def list_meetings(self, *, cursor: str | None, limit: int) -> MeetingPage:
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1–100 之间")
        cursor_values = _decode_cursor(cursor) if cursor else None
        async with self._connection() as connection:
            if cursor_values is None:
                query = (
                    f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings "
                    "ORDER BY created_at DESC, id DESC LIMIT %s"
                )
                params: tuple[Any, ...] = (limit + 1,)
            else:
                query = (
                    f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings "
                    "WHERE (created_at, id) < (%s, %s) "
                    "ORDER BY created_at DESC, id DESC LIMIT %s"
                )
                params = (*cursor_values, limit + 1)
            result = await connection.execute(query, params)
            rows = await result.fetchall()
        records = [_meeting_from_row(row) for row in rows]
        next_cursor = None
        if len(records) > limit:
            last = records[limit - 1]
            next_cursor = _encode_cursor(last.created_at, last.id)
            records = records[:limit]
        return MeetingPage(items=tuple(records), next_cursor=next_cursor)

    async def update_title(self, meeting_id: UUID, title: str) -> MeetingRecord:
        """更新会议标题；标题不改变内容 revision。"""
        title = _validate_title(title)
        async with self._connection() as connection, connection.transaction():
            meeting = await self._lock_meeting(connection, meeting_id)
            if meeting is None:
                raise MeetingNotFoundError("会议不存在")
            if meeting.title == title:
                return meeting
            now = _utc_now()
            cursor = await connection.execute(
                f"""
                UPDATE {self._schema}.meetings
                SET title = %s, updated_at = %s
                WHERE id = %s
                RETURNING {_MEETING_COLUMNS}
                """,
                (title, now, meeting_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RepositoryUnavailableError("标题更新后无法读取会议")
            await self._insert_event(
                connection,
                meeting_id,
                "meeting_title_updated",
                {"title": title},
            )
            return _meeting_from_row(row)

    async def set_status(
        self, meeting_id: UUID, status: MeetingStatus, *, reason: str | None = None
    ) -> MeetingRecord:
        if reason is not None:
            reason = reason.strip()
            if len(reason) > 128 or any(ord(char) < 32 for char in reason):
                raise ValueError("中断原因无效")
        async with self._connection() as connection, connection.transaction():
            meeting = await self._lock_meeting(connection, meeting_id)
            if meeting is None:
                raise MeetingNotFoundError("会议不存在")
            self._ensure_transition(meeting.status, status)
            now = _utc_now()
            ended_at = now if status in {
                MeetingStatus.COMPLETED,
                MeetingStatus.INTERRUPTED,
                MeetingStatus.STORAGE_ERROR,
            } else meeting.ended_at
            await connection.execute(
                f"""
                    UPDATE {self._schema}.meetings
                    SET status = %s, interruption_reason = %s, ended_at = %s, updated_at = %s
                    WHERE id = %s
                    """,
                (status.value, reason, ended_at, now, meeting_id),
            )
            await self._insert_event(
                connection,
                meeting_id,
                "meeting_state_changed",
                {"status": status.value, "reason": reason} if reason else {"status": status.value},
            )
            cursor = await connection.execute(
                f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings WHERE id = %s",
                (meeting_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RepositoryUnavailableError("状态更新后无法读取会议")
            return _meeting_from_row(row)

    async def reconcile_window(
        self, meeting_id: UUID, window: TranscriptWindow
    ) -> TranscriptReconcileResult:
        segments = tuple(
            sorted(window.segments, key=lambda item: (item.start_ms, item.end_ms, item.order))
        )
        replace_from_ms = min((segment.start_ms for segment in segments), default=0)
        async with self._connection() as connection, connection.transaction():
            meeting = await self._lock_meeting(connection, meeting_id)
            if meeting is None:
                raise MeetingNotFoundError("会议不存在")
            if meeting.status not in {MeetingStatus.RECORDING, MeetingStatus.FINALIZING}:
                raise MeetingConflictError("会议已不再接受转录")
            if not segments:
                return TranscriptReconcileResult(
                    meeting_id=meeting_id,
                    transcript_revision=meeting.transcript_revision,
                    content_revision=meeting.content_revision,
                    replace_from_ms=replace_from_ms,
                    segments=(),
                )
            current_cursor = await connection.execute(
                f"""
                SELECT {_SEGMENT_COLUMNS}
                FROM {self._schema}.transcript_segments
                WHERE meeting_id = %s AND end_ms >= %s
                ORDER BY start_ms, end_ms, segment_order
                """,
                (meeting_id, replace_from_ms),
            )
            current_rows = await current_cursor.fetchall()
            current_signature = [
                tuple(row[index] for index in range(0, 9)) for row in current_rows
            ]
            desired_signature = [
                (
                    segment.id,
                    segment.order,
                    segment.source_epoch,
                    segment.speaker_key,
                    segment.start_ms,
                    segment.end_ms,
                    segment.text,
                    segment.translation,
                    segment.detected_language,
                )
                for segment in segments
            ]
            if current_signature == desired_signature:
                return TranscriptReconcileResult(
                    meeting_id=meeting_id,
                    transcript_revision=meeting.transcript_revision,
                    content_revision=meeting.content_revision,
                    replace_from_ms=replace_from_ms,
                    segments=segments,
                )

            await connection.execute(
                f"DELETE FROM {self._schema}.transcript_segments "
                "WHERE meeting_id = %s AND end_ms >= %s",
                (meeting_id, replace_from_ms),
            )
            for segment in segments:
                await connection.execute(
                    f"""
                        INSERT INTO {self._schema}.transcript_segments
                            (id, meeting_id, segment_order, source_epoch, speaker_key,
                             start_ms, end_ms, text, translation, detected_language)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            segment_order = EXCLUDED.segment_order,
                            source_epoch = EXCLUDED.source_epoch,
                            speaker_key = EXCLUDED.speaker_key,
                            start_ms = EXCLUDED.start_ms,
                            end_ms = EXCLUDED.end_ms,
                            text = EXCLUDED.text,
                            translation = EXCLUDED.translation,
                            detected_language = EXCLUDED.detected_language,
                            updated_at = now()
                        """,
                    (
                        segment.id,
                        meeting_id,
                        segment.order,
                        segment.source_epoch,
                        segment.speaker_key,
                        segment.start_ms,
                        segment.end_ms,
                        segment.text,
                        segment.translation,
                        segment.detected_language,
                    ),
                )
                await self._upsert_speaker(connection, meeting_id, segment)
            now = _utc_now()
            transcript_revision = meeting.transcript_revision + 1
            content_revision = meeting.content_revision + 1
            await connection.execute(
                f"""
                    UPDATE {self._schema}.meetings
                    SET transcript_revision = %s, content_revision = %s, updated_at = %s
                    WHERE id = %s
                    """,
                (transcript_revision, content_revision, now, meeting_id),
            )
            await self._insert_event(
                connection,
                meeting_id,
                "transcript_reconciled",
                {
                    "transcript_revision": transcript_revision,
                    "content_revision": content_revision,
                    "replace_from_ms": replace_from_ms,
                    "segment_count": len(segments),
                },
            )
            return TranscriptReconcileResult(
                meeting_id=meeting_id,
                transcript_revision=transcript_revision,
                content_revision=content_revision,
                replace_from_ms=replace_from_ms,
                segments=segments,
            )


    # ------------------------------------------------------------------
    # SPK-E2E-1 speaker-only 归属事务（不重用 reconcile_window 的后缀替换）
    # ------------------------------------------------------------------

    async def append_completed_item(
        self, meeting_id: UUID, item: CompletedItem
    ) -> TranscriptReconcileResult | None:
        """追加一次 commit 的固定正文单元；重复 event 幂等返回 None。

        正文、时间、身份在落库后不可变；speaker-only 修订走
        :meth:`apply_speaker_patches`，禁止用后缀替换实现归属更新。
        """
        payload_hash = canonical_payload_hash({"item": item.model_dump(mode="json")})
        async with self._connection() as connection:  # noqa: SIM117
            async with connection.transaction():
                meeting = await self._lock_meeting(connection, meeting_id)
                if meeting is None:
                    raise MeetingNotFoundError("会议不存在")
                if meeting.status not in {MeetingStatus.RECORDING, MeetingStatus.FINALIZING}:
                    raise MeetingConflictError("会议已不再接受转录")
                credential = await self._source_event_credential(
                    connection, meeting_id, item.source_session_id, item.event_id
                )
                if credential is not None:
                    if credential != payload_hash:
                        raise MeetingConflictError("同 source event 内容冲突")
                    return None

                await self._register_transcription_source(
                    connection, meeting_id, item
                )
                rows = await self._insert_completed_segments(connection, meeting_id, item)
                if not rows:
                    # 空 transcript 的 completed 也推进水位与版本事实。
                    rows = []
                transcript_revision = meeting.transcript_revision + 1
                content_revision = meeting.content_revision + 1
                await connection.execute(
                    f"""
                    UPDATE {self._schema}.meetings
                    SET transcript_revision = %s, content_revision = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (transcript_revision, content_revision, _utc_now(), meeting_id),
                )
                await self._insert_event(
                    connection,
                    meeting_id,
                    "completed_item_appended",
                    {
                        "session_id": item.source_session_id,
                        "item_id": item.item_id,
                        "sequence": item.sequence,
                        "segment_count": len(rows),
                    },
                )
                await connection.execute(
                    f"""
                    INSERT INTO {self._schema}.meeting_source_events
                        (meeting_id, session_id, event_id, event_kind, canonical_payload_hash)
                    VALUES (%s, %s, %s, 'completed', %s)
                    """,
                    (meeting_id, item.source_session_id, item.event_id, payload_hash),
                )
                segments = tuple(
                    NormalizedSegment(
                        id=row_id,
                        order=order,
                        source_epoch=item.source_epoch,
                        speaker_key=speaker_key,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        text=text,
                        speaker_status=SPEAKER_KEY_UNKNOWN,
                        timing_quality=timing_quality,
                    )
                    for row_id, order, speaker_key, start_ms, end_ms, text,
                    timing_quality in rows
                )
                replace_from_ms = min(
                    (segment.start_ms for segment in segments), default=0
                )
                return TranscriptReconcileResult(
                    meeting_id=meeting_id,
                    transcript_revision=transcript_revision,
                    content_revision=content_revision,
                    replace_from_ms=replace_from_ms,
                    segments=segments,
                )

    async def _register_transcription_source(
        self, connection: Any, meeting_id: UUID, item: CompletedItem
    ) -> None:
        """登记/推进 source epoch 时钟；一个 session 只能绑定一个 epoch。"""
        cursor = await connection.execute(
            f"""
            SELECT source_epoch FROM {self._schema}.meeting_transcription_sources
            WHERE meeting_id = %s AND session_id = %s
            """,
            (meeting_id, item.source_session_id),
        )
        existing = await cursor.fetchone()
        if existing is not None and int(existing[0]) != item.source_epoch:
            raise MeetingConflictError("source session 已绑定其他 epoch")
        committed_meeting_sample = item.meeting_start_sample + item.audio_end_sample
        await connection.execute(
            f"""
            INSERT INTO {self._schema}.meeting_transcription_sources
                (meeting_id, source_epoch, session_id, meeting_start_sample,
                 last_committed_meeting_sample)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (meeting_id, source_epoch) DO UPDATE SET
                last_committed_meeting_sample = GREATEST(
                    {self._schema}.meeting_transcription_sources.last_committed_meeting_sample,
                    EXCLUDED.last_committed_meeting_sample
                ),
                updated_at = now()
            """,
            (
                meeting_id,
                item.source_epoch,
                item.source_session_id,
                item.meeting_start_sample,
                committed_meeting_sample,
            ),
        )

    async def _insert_completed_segments(
        self, connection: Any, meeting_id: UUID, item: CompletedItem
    ) -> list[tuple[UUID, int, str, int, int, str, str]]:
        """把 completed 的归属单元逐个落为不可变正文段（speaker 初始 unknown）。"""
        cursor = await connection.execute(
            f"""
            SELECT coalesce(max(segment_order) + 1, 0)
            FROM {self._schema}.transcript_segments
            WHERE meeting_id = %s
            """,
            (meeting_id,),
        )
        row = await cursor.fetchone()
        next_order = int(row[0]) if row and row[0] is not None else 0
        inserted: list[tuple[UUID, int, str, int, int, str, str]] = []
        for unit in item.units:
            text = item.canonical_text[unit.text_start : unit.text_end]
            if not text.strip():
                continue
            start_ms = (item.meeting_start_sample + unit.audio_start_sample) // 16
            end_ms = (item.meeting_start_sample + unit.audio_end_sample) // 16
            segment_id = segment_identity(
                meeting_id, item.source_session_id, unit.segment_uid
            )
            await connection.execute(
                f"""
                INSERT INTO {self._schema}.transcript_segments
                    (id, meeting_id, segment_order, source_epoch, speaker_key,
                     start_ms, end_ms, text, source_session_id, source_segment_uid,
                     source_item_id, speaker_status, timing_quality)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    segment_id,
                    meeting_id,
                    next_order,
                    item.source_epoch,
                    SPEAKER_KEY_UNKNOWN,
                    start_ms,
                    end_ms,
                    text,
                    item.source_session_id,
                    unit.segment_uid,
                    item.item_id,
                    SPEAKER_KEY_UNKNOWN,
                    unit.timing_quality,
                ),
            )
            next_order += 1
            inserted.append(
                (
                    segment_id,
                    next_order - 1,
                    SPEAKER_KEY_UNKNOWN,
                    start_ms,
                    end_ms,
                    text,
                    unit.timing_quality,
                )
            )
        return inserted

    async def apply_speaker_patches(
        self, meeting_id: UUID, event: SpeakerPatchEvent
    ) -> SpeakerPatchResult:
        """speaker-only 幂等修订事务：整批校验、人工优先、一次版本。"""
        payload_hash = canonical_payload_hash({"event": event.model_dump(mode="json")})
        async with self._connection() as connection:  # noqa: SIM117
            async with connection.transaction():
                meeting = await self._lock_meeting(connection, meeting_id)
                if meeting is None:
                    raise MeetingNotFoundError("会议不存在")
                if meeting.status not in {MeetingStatus.RECORDING, MeetingStatus.FINALIZING}:
                    raise MeetingConflictError("会议已不再接受归属修订")
                credential = await self._source_event_credential(
                    connection, meeting_id, event.source_session_id, event.event_id
                )
                if credential is not None:
                    if credential != payload_hash:
                        raise MeetingConflictError("同 source event 内容冲突")
                    return SpeakerPatchResult(
                        meeting_id=meeting_id,
                        transcript_revision=meeting.transcript_revision,
                        content_revision=meeting.content_revision,
                        diarization_status=cast(
                            Literal["off", "active", "complete", "degraded"],
                            meeting.diarization_status.value,
                        ),
                    )

                source_cursor = await connection.execute(
                    f"""
                    SELECT 1 FROM {self._schema}.meeting_transcription_sources
                    WHERE meeting_id = %s AND session_id = %s
                    """,
                    (meeting_id, event.source_session_id),
                )
                if await source_cursor.fetchone() is None:
                    raise MeetingConflictError("未知 source session，整批拒绝")

                target_rows = await self._load_patch_targets(
                    connection, meeting_id, event
                )
                targets = {
                    row[0]: (row[1], row[2]) for row in target_rows
                }
                errors = patch_target_errors(event, targets)
                if errors:
                    raise MeetingConflictError(f"归属修订目标校验失败: {errors[0]}")

                changed: list[UUID] = []
                for patch in event.patches:
                    target = next(
                        row for row in target_rows if row[0] == patch.segment_uid
                    )
                    override_key = target[3]
                    old_key = target[5]
                    segment_id = target[6]
                    model_key = (
                        await self._ensure_speaker_source(
                            connection,
                            meeting_id,
                            event.source_session_id,
                            patch.source_speaker,
                        )
                        if patch.source_speaker is not None
                        else None
                    )
                    speaker_key, model_speaker_key, _override = resolve_patch_application(
                        override_key=override_key,
                        model_key=model_key,
                        status=patch.status,
                    )
                    frozen = patch.status == "stable"
                    await connection.execute(
                        f"""
                        UPDATE {self._schema}.transcript_segments
                        SET speaker_key = %s,
                            model_speaker_key = %s,
                            speaker_revision = %s,
                            speaker_status = %s,
                            speaker_frozen = %s,
                            coverage_ratio = %s,
                            overlap_ratio = %s,
                            speaker_candidates = %s,
                            updated_at = %s
                        WHERE id = %s
                        """,
                        (
                            speaker_key,
                            model_speaker_key,
                            patch.revision,
                            patch.status,
                            frozen,
                            patch.coverage_ratio,
                            patch.overlap_ratio,
                            Jsonb(
                                [
                                    {"speaker": c.source_speaker, "support_ratio": c.support_ratio}
                                    for c in patch.candidates
                                ]
                            ),
                            _utc_now(),
                            segment_id,
                        ),
                    )
                    if speaker_key != old_key:
                        changed.append(segment_id)

                await connection.execute(
                    f"""
                    UPDATE {self._schema}.meeting_transcription_sources
                    SET stable_through_sample = GREATEST(stable_through_sample, %s),
                        last_update_sequence = GREATEST(last_update_sequence, %s),
                        updated_at = now()
                    WHERE meeting_id = %s AND session_id = %s
                    """,
                    (
                        event.stable_through_sample,
                        event.sequence,
                        meeting_id,
                        event.source_session_id,
                    ),
                )

                current_status = meeting.diarization_status
                if changed or current_status in {DiarizationStatus.OFF, DiarizationStatus.LEGACY}:
                    transcript_revision = meeting.transcript_revision + 1
                    content_revision = meeting.content_revision + 1
                    next_status: DiarizationStatus = (
                        DiarizationStatus.ACTIVE
                        if current_status in {DiarizationStatus.OFF, DiarizationStatus.LEGACY}
                        else current_status
                    )
                    await connection.execute(
                        f"""
                        UPDATE {self._schema}.meetings
                        SET transcript_revision = %s, content_revision = %s,
                            diarization_status = %s, updated_at = %s
                        WHERE id = %s
                        """,
                        (
                            transcript_revision,
                            content_revision,
                            next_status.value,
                            _utc_now(),
                            meeting_id,
                        ),
                    )
                    if changed:
                        await self._insert_event(
                            connection,
                            meeting_id,
                            "speaker_patched",
                            {
                                "session_id": event.source_session_id,
                                "event_id": event.event_id,
                                "sequence": event.sequence,
                                "changed_count": len(changed),
                                "transcript_revision": transcript_revision,
                                "content_revision": content_revision,
                            },
                        )
                else:
                    transcript_revision = meeting.transcript_revision
                    content_revision = meeting.content_revision
                    next_status = current_status
                await connection.execute(
                    f"""
                    INSERT INTO {self._schema}.meeting_source_events
                        (meeting_id, session_id, event_id, event_kind, canonical_payload_hash)
                    VALUES (%s, %s, %s, 'patch', %s)
                    """,
                    (meeting_id, event.source_session_id, event.event_id, payload_hash),
                )
                suffix_segments: tuple[NormalizedSegment, ...] = ()
                if changed:
                    suffix_segments = await self._affected_suffix(
                        connection, meeting_id, changed
                    )
                return SpeakerPatchResult(
                    meeting_id=meeting_id,
                    changed_segment_ids=tuple(changed),
                    transcript_revision=transcript_revision,
                    content_revision=content_revision,
                    diarization_status=cast(
                        Literal["off", "active", "complete", "degraded"],
                        next_status.value,
                    ),
                    segments=suffix_segments,
                )

    async def _affected_suffix(
        self, connection: Any, meeting_id: UUID, changed_ids: list[UUID]
    ) -> tuple[NormalizedSegment, ...]:
        """返回 end_ms >= 最小被改 start 的完整后缀（presenter replace 语义）。"""
        start_cursor = await connection.execute(
            f"""
            SELECT min(start_ms) FROM {self._schema}.transcript_segments
            WHERE meeting_id = %s AND id = ANY(%s)
            """,
            (meeting_id, changed_ids),
        )
        row = await start_cursor.fetchone()
        min_start = int(row[0]) if row is not None and row[0] is not None else 0
        suffix_cursor = await connection.execute(
            f"""
            SELECT {_SEGMENT_COLUMNS_DETAIL}
            FROM {self._schema}.transcript_segments
            WHERE meeting_id = %s AND end_ms >= %s
            ORDER BY segment_order, start_ms, id
            """,
            (meeting_id, min_start),
        )
        return tuple(
            _segment_from_detail_row(suffix_row)
            for suffix_row in await suffix_cursor.fetchall()
        )

    async def _source_event_credential(
        self, connection: Any, meeting_id: UUID, session_id: str, event_id: str
    ) -> str | None:
        cursor = await connection.execute(
            f"""
            SELECT canonical_payload_hash FROM {self._schema}.meeting_source_events
            WHERE meeting_id = %s AND session_id = %s AND event_id = %s
            """,
            (meeting_id, session_id, event_id),
        )
        row = await cursor.fetchone()
        return str(row[0]) if row is not None else None

    async def _load_patch_targets(
        self, connection: Any, meeting_id: UUID, event: SpeakerPatchEvent
    ) -> list[tuple[str, int, bool, str | None, str | None, str, UUID]]:
        uids = [patch.segment_uid for patch in event.patches]
        if not uids:
            return []
        cursor = await connection.execute(
            f"""
            SELECT source_segment_uid, speaker_revision, speaker_frozen,
                   speaker_override_key, model_speaker_key, speaker_key, id
            FROM {self._schema}.transcript_segments
            WHERE meeting_id = %s AND source_session_id = %s
              AND source_segment_uid = ANY(%s)
            """,
            (meeting_id, event.source_session_id, uids),
        )
        rows = await cursor.fetchall()
        return [
            (
                str(row[0]),
                int(row[1]),
                bool(row[2]),
                cast(str | None, row[3]),
                cast(str | None, row[4]),
                str(row[5]),
                cast(UUID, row[6]),
            )
            for row in rows
        ]

    async def _ensure_speaker_source(
        self,
        connection: Any,
        meeting_id: UUID,
        session_id: str,
        source_speaker: str,
    ) -> str:
        """解析/登记 (session, 匿名标签) → 会议内不透明应用身份。"""
        application_key = speaker_source_key(meeting_id, session_id, source_speaker)
        await connection.execute(
            f"""
            INSERT INTO {self._schema}.meeting_speaker_sources
                (meeting_id, session_id, source_speaker, application_speaker_key)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (meeting_id, session_id, source_speaker) DO UPDATE SET
                updated_at = now()
            """,
            (meeting_id, session_id, source_speaker, application_key),
        )

        epoch_cursor = await connection.execute(
            f"""
            SELECT source_epoch FROM {self._schema}.meeting_transcription_sources
            WHERE meeting_id = %s AND session_id = %s
            """,
            (meeting_id, session_id),
        )
        epoch_row = await epoch_cursor.fetchone()
        source_epoch = int(epoch_row[0]) if epoch_row and epoch_row[0] is not None else 0

        default_label = speaker_display_label(application_key, source_speaker)
        await connection.execute(
            f"""
            INSERT INTO {self._schema}.meeting_speakers
                (meeting_id, speaker_key, source_epoch, raw_speaker, default_label, display_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (meeting_id, speaker_key) DO UPDATE SET
                source_epoch = EXCLUDED.source_epoch,
                raw_speaker = EXCLUDED.raw_speaker,
                default_label = EXCLUDED.default_label,
                display_name = CASE
                    WHEN {self._schema}.meeting_speakers.display_name
                         != {self._schema}.meeting_speakers.default_label
                    THEN {self._schema}.meeting_speakers.display_name
                    ELSE EXCLUDED.display_name
                END,
                updated_at = now()
            """,
            (
                meeting_id,
                application_key,
                source_epoch,
                source_speaker,
                default_label,
                default_label,
            ),
        )
        return application_key

    async def get_diarization_watermark(self, meeting_id: UUID, session_id: str) -> int:
        """返回 source 的 last_update_sequence（分人 patch 已持久化水位）。"""
        async with self._connection() as connection:
            cursor = await connection.execute(
                f"""
                SELECT last_update_sequence FROM {self._schema}.meeting_transcription_sources
                WHERE meeting_id = %s AND session_id = %s
                """,
                (meeting_id, session_id),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row is not None and row[0] is not None else 0

    async def set_speaker_override(
        self, meeting_id: UUID, segment_id: UUID, override_key: str
    ) -> None:
        """人工改归属：写 override 并立即生效；后续自动修订只更新模型证据。"""
        override_key = override_key.strip()
        if not override_key or len(override_key) > 200:
            raise ValueError("override speaker key 无效")
        async with self._connection() as connection:  # noqa: SIM117
            async with connection.transaction():
                meeting = await self._lock_meeting(connection, meeting_id)
                if meeting is None:
                    raise MeetingNotFoundError("会议不存在")
                cursor = await connection.execute(
                    f"""
                    UPDATE {self._schema}.transcript_segments
                    SET speaker_override_key = %s, speaker_key = %s, updated_at = %s
                    WHERE meeting_id = %s AND id = %s
                    RETURNING id
                    """,
                    (override_key, override_key, _utc_now(), meeting_id, segment_id),
                )
                if await cursor.fetchone() is None:
                    raise MeetingNotFoundError("归属目标 segment 不存在")
                await self._bump_revisions_for_manual_change(
                    connection, meeting_id, meeting, "speaker_override_set",
                    {"segment_id": str(segment_id), "override_key": override_key},
                )

    async def clear_speaker_override(self, meeting_id: UUID, segment_id: UUID) -> None:
        """撤销人工归属：显式清空 override，恢复使用最新模型值。"""
        async with self._connection() as connection:  # noqa: SIM117
            async with connection.transaction():
                meeting = await self._lock_meeting(connection, meeting_id)
                if meeting is None:
                    raise MeetingNotFoundError("会议不存在")
                cursor = await connection.execute(
                    f"""
                    UPDATE {self._schema}.transcript_segments
                    SET speaker_override_key = NULL,
                        speaker_key = coalesce(model_speaker_key, %s),
                        updated_at = %s
                    WHERE meeting_id = %s AND id = %s
                    RETURNING id
                    """,
                    (SPEAKER_KEY_UNKNOWN, _utc_now(), meeting_id, segment_id),
                )
                if await cursor.fetchone() is None:
                    raise MeetingNotFoundError("归属目标 segment 不存在")
                await self._bump_revisions_for_manual_change(
                    connection, meeting_id, meeting, "speaker_override_cleared",
                    {"segment_id": str(segment_id)},
                )

    async def _bump_revisions_for_manual_change(
        self,
        connection: Any,
        meeting_id: UUID,
        meeting: MeetingRecord,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """人工更正递增版本；纪要 worker 据此把旧结果标 stale。"""
        transcript_revision = meeting.transcript_revision + 1
        content_revision = meeting.content_revision + 1
        await connection.execute(
            f"""
            UPDATE {self._schema}.meetings
            SET transcript_revision = %s, content_revision = %s, updated_at = %s
            WHERE id = %s
            """,
            (transcript_revision, content_revision, _utc_now(), meeting_id),
        )
        await self._insert_event(connection, meeting_id, event_type, payload)

    async def finalize_diarization(
        self,
        meeting_id: UUID,
        *,
        status: str,
        reason: str | None = None,
    ) -> MeetingRecord:
        """记录分人终态（complete/degraded）；不改变会议持久状态。"""
        if status not in {"complete", "degraded"}:
            raise ValueError("diarization 终态必须是 complete 或 degraded")
        if reason is not None:
            reason = reason.strip() or None
            if reason is not None and (
                len(reason) > 128 or any(ord(char) < 32 for char in reason)
            ):
                raise ValueError("diarization 原因无效")
        async with self._connection() as connection:  # noqa: SIM117
            async with connection.transaction():
                meeting = await self._lock_meeting(connection, meeting_id)
                if meeting is None:
                    raise MeetingNotFoundError("会议不存在")
                target = (
                    DiarizationStatus.COMPLETE
                    if status == "complete"
                    else DiarizationStatus.DEGRADED
                )
                if meeting.diarization_status is target and meeting.diarization_reason == reason:
                    return meeting
                cursor = await connection.execute(
                    f"""
                    UPDATE {self._schema}.meetings
                    SET diarization_status = %s, diarization_reason = %s, updated_at = %s
                    WHERE id = %s
                    RETURNING {_MEETING_COLUMNS}
                    """,
                    (target.value, reason, _utc_now(), meeting_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RepositoryUnavailableError("diarization 终态写入后无法读取会议")
                await self._insert_event(
                    connection,
                    meeting_id,
                    "diarization_terminal",
                    {"status": status, "reason": reason},
                )
                return _meeting_from_row(row)

    async def finalize_transcript(
        self,
        meeting_id: UUID,
        *,
        final_status: MeetingStatus = MeetingStatus.COMPLETED,
        reason: str | None = None,
    ) -> MeetingRecord:
        if final_status not in {MeetingStatus.COMPLETED, MeetingStatus.INTERRUPTED}:
            raise ValueError("封存终态必须是 completed 或 interrupted")
        if reason is not None:
            reason = reason.strip()
            if len(reason) > 128 or any(ord(char) < 32 for char in reason):
                raise ValueError("中断原因无效")
        async with self._connection() as connection:  # noqa: SIM117
            async with connection.transaction():
                meeting = await self._lock_meeting(connection, meeting_id)
                if meeting is None:
                    raise MeetingNotFoundError("会议不存在")
                if meeting.status == final_status:
                    return meeting
                if meeting.status not in {MeetingStatus.RECORDING, MeetingStatus.FINALIZING}:
                    raise MeetingConflictError("会议无法封存")
                await connection.execute(
                    f"""
                    WITH ordered AS (
                        SELECT id,
                               row_number() OVER (ORDER BY start_ms, end_ms, id) - 1 AS new_order
                        FROM {self._schema}.transcript_segments
                        WHERE meeting_id = %s
                    )
                    UPDATE {self._schema}.transcript_segments AS segments
                    SET segment_order = ordered.new_order, updated_at = now()
                    FROM ordered
                    WHERE segments.id = ordered.id
                    """,
                    (meeting_id,),
                )
                now = _utc_now()
                await connection.execute(
                    f"""
                    UPDATE {self._schema}.meetings
                    SET status = %s, interruption_reason = %s, ended_at = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (final_status.value, reason, now, now, meeting_id),
                )
                await self._insert_event(
                    connection,
                    meeting_id,
                    (
                        "meeting_completed"
                        if final_status is MeetingStatus.COMPLETED
                        else "meeting_interrupted"
                    ),
                    {"reason": reason} if reason else {},
                )
                cursor = await connection.execute(
                    f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings WHERE id = %s",
                    (meeting_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RepositoryUnavailableError("封存后无法读取会议")
                return _meeting_from_row(row)

    async def get_transcript(self, meeting_id: UUID) -> TranscriptDocument:
        async with self._connection() as connection:
            meeting = await self._lock_meeting(connection, meeting_id, lock=False)
            if meeting is None:
                raise MeetingNotFoundError("会议不存在")
            segment_cursor = await connection.execute(
                f"""
                SELECT {_SEGMENT_COLUMNS_DETAIL}
                FROM {self._schema}.transcript_segments
                WHERE meeting_id = %s
                ORDER BY segment_order, start_ms, id
                """,
                (meeting_id,),
            )
            segment_rows = await segment_cursor.fetchall()
            segments = tuple(
                NormalizedSegment(
                    id=cast(UUID, row[0]),
                    order=int(row[1]),
                    source_epoch=int(row[2]),
                    speaker_key=str(row[3]),
                    start_ms=int(row[4]),
                    end_ms=int(row[5]),
                    text=str(row[6]),
                    translation=cast(str | None, row[7]),
                    detected_language=cast(str | None, row[8]),
                    speaker_status=cast(str | None, row[9]),
                    timing_quality=cast(str | None, row[10]),
                    overlap_ratio=float(row[11]),
                    speaker_manual=bool(row[12]),
                )
                for row in segment_rows
            )
            speaker_cursor = await connection.execute(
                f"""
                SELECT meeting_id, speaker_key, source_epoch, raw_speaker,
                       default_label, display_name, created_at, updated_at
                FROM {self._schema}.meeting_speakers
                WHERE meeting_id = %s
                ORDER BY source_epoch, speaker_key
                """,
                (meeting_id,),
            )
            speaker_rows = await speaker_cursor.fetchall()
            speakers = tuple(
                SpeakerRecord(
                    meeting_id=cast(UUID, row[0]),
                    speaker_key=str(row[1]),
                    source_epoch=int(row[2]),
                    raw_speaker=str(row[3]),
                    default_label=str(row[4]),
                    display_name=str(row[5]),
                    created_at=cast(datetime, row[6]),
                    updated_at=cast(datetime, row[7]),
                )
                for row in speaker_rows
            )
            return TranscriptDocument(
                meeting_id=meeting_id,
                transcript_revision=meeting.transcript_revision,
                content_revision=meeting.content_revision,
                segments=segments,
                speakers=speakers,
            )

    async def get_speakers(self, meeting_id: UUID) -> tuple[SpeakerRecord, ...]:
        """读取会议内匿名 speaker 映射，供详情 API 使用。"""
        async with self._connection() as connection:
            if await self._lock_meeting(connection, meeting_id, lock=False) is None:
                raise MeetingNotFoundError("会议不存在")
            cursor = await connection.execute(
                f"""
                SELECT meeting_id, speaker_key, source_epoch, raw_speaker,
                       default_label, display_name, created_at, updated_at
                FROM {self._schema}.meeting_speakers
                WHERE meeting_id = %s
                ORDER BY source_epoch, speaker_key
                """,
                (meeting_id,),
            )
            rows = await cursor.fetchall()
            return tuple(
                SpeakerRecord(
                    meeting_id=cast(UUID, row[0]),
                    speaker_key=str(row[1]),
                    source_epoch=int(row[2]),
                    raw_speaker=str(row[3]),
                    default_label=str(row[4]),
                    display_name=str(row[5]),
                    created_at=cast(datetime, row[6]),
                    updated_at=cast(datetime, row[7]),
                )
                for row in rows
            )

    async def get_latest_minutes(self, meeting_id: UUID) -> MinutesRecord | None:
        """读取最新纪要版本；会议不存在时抛出稳定 not-found 错误。"""
        async with self._connection() as connection:
            if await self._lock_meeting(connection, meeting_id, lock=False) is None:
                raise MeetingNotFoundError("会议不存在")
            cursor = await connection.execute(
                f"""
                SELECT {_MINUTES_COLUMNS}
                FROM {self._schema}.meeting_minutes
                WHERE meeting_id = %s
                ORDER BY version DESC
                LIMIT 1
                """,
                (meeting_id,),
            )
            row = await cursor.fetchone()
            return _minutes_from_row(row) if row is not None else None

    async def get_minutes(self, meeting_id: UUID, version: int) -> MinutesRecord | None:
        """读取指定纪要版本。"""
        if version < 1:
            raise ValueError("纪要版本必须为正整数")
        async with self._connection() as connection:
            if await self._lock_meeting(connection, meeting_id, lock=False) is None:
                raise MeetingNotFoundError("会议不存在")
            cursor = await connection.execute(
                f"""
                SELECT {_MINUTES_COLUMNS}
                FROM {self._schema}.meeting_minutes
                WHERE meeting_id = %s AND version = %s
                """,
                (meeting_id, version),
            )
            row = await cursor.fetchone()
            return _minutes_from_row(row) if row is not None else None

    async def rename_speaker(
        self, meeting_id: UUID, speaker_key: str, display_name: str
    ) -> MeetingRecord:
        speaker_key = speaker_key.strip()
        display_name = _validate_display_name(display_name)
        async with self._connection() as connection, connection.transaction():
            meeting = await self._lock_meeting(connection, meeting_id)
            if meeting is None:
                raise MeetingNotFoundError("会议不存在")
            cursor = await connection.execute(
                f"""
                SELECT display_name FROM {self._schema}.meeting_speakers
                WHERE meeting_id = %s AND speaker_key = %s
                FOR UPDATE
                """,
                (meeting_id, speaker_key),
            )
            row = await cursor.fetchone()
            if row is None:
                raise MeetingNotFoundError("说话人不存在")
            if str(row[0]) == display_name:
                return meeting
            raw_speaker = speaker_key.rsplit(":", 1)[-1]
            now = _utc_now()
            await connection.execute(
                f"""
                UPDATE {self._schema}.meeting_speakers
                SET display_name = %s, updated_at = %s
                WHERE meeting_id = %s AND (speaker_key = %s OR raw_speaker = %s)
                """,
                (display_name, now, meeting_id, speaker_key, raw_speaker),
            )
            content_revision = meeting.content_revision + 1
            await connection.execute(
                f"""
                UPDATE {self._schema}.meetings
                SET content_revision = %s, updated_at = %s
                WHERE id = %s
                """,
                (content_revision, now, meeting_id),
            )
            await self._insert_event(
                connection,
                meeting_id,
                "speaker_updated",
                {"speaker_key": speaker_key, "content_revision": content_revision},
            )
            cursor = await connection.execute(
                f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings WHERE id = %s",
                (meeting_id,),
            )
            updated = await cursor.fetchone()
            if updated is None:
                raise RepositoryUnavailableError("说话人更新后无法读取会议")
            return _meeting_from_row(updated)

    async def apply_speaker_remapping(
        self, meeting_id: UUID, remapping: dict[str, str]
    ) -> MeetingRecord:
        """会后将聚类后的说话人重映射原子应用至数据库，更新 segments 与 speakers。"""
        if not remapping:
            record = await self.get_meeting(meeting_id)
            if record is None:
                raise MeetingNotFoundError("会议不存在")
            return record

        async with self._connection() as connection, connection.transaction():
            meeting = await self._lock_meeting(connection, meeting_id)
            if meeting is None:
                raise MeetingNotFoundError("会议不存在")

            now = _utc_now()
            affected = [key for key, dst in remapping.items() if key != dst]
            if affected:
                # 按原快照一次计算每段目标 key：A↔B 交换不得用循环 UPDATE
                # 逐键重写（那会让两组坍缩成同一个值）。
                snapshot_cursor = await connection.execute(
                    f"""
                    SELECT id, speaker_key FROM {self._schema}.transcript_segments
                    WHERE meeting_id = %s AND speaker_key = ANY(%s)
                    """,
                    (meeting_id, affected),
                )
                snapshot = await snapshot_cursor.fetchall()
                updates = [
                    (remapping[str(row[1])], row[0])
                    for row in snapshot
                    if remapping.get(str(row[1]), str(row[1])) != str(row[1])
                ]
                if updates:
                    await connection.execute(
                        f"""
                        UPDATE {self._schema}.transcript_segments AS segments
                        SET speaker_key = v.key, updated_at = %s
                        FROM unnest(%s::uuid[], %s::text[]) AS v(id, key)
                        WHERE segments.id = v.id
                        """,
                        (
                            now,
                            [row[1] for row in updates],
                            [row[0] for row in updates],
                        ),
                    )
            for src_spk, dst_spk in remapping.items():
                if src_spk == dst_spk:
                    continue
                # 检查源说话人是否有自定义名称
                src_cursor = await connection.execute(
                    f"""
                    SELECT display_name, default_label FROM {self._schema}.meeting_speakers
                    WHERE meeting_id = %s AND speaker_key = %s
                    """,
                    (meeting_id, src_spk),
                )
                src_row = await src_cursor.fetchone()
                if src_row:
                    src_display, src_default = str(src_row[0]), str(src_row[1])
                    if src_display != src_default:
                        # 尝试将自定义名称迁移至目标说话人（若目标尚为默认名称）
                        await connection.execute(
                            f"""
                            UPDATE {self._schema}.meeting_speakers
                            SET display_name = %s, updated_at = %s
                            WHERE meeting_id = %s
                              AND speaker_key = %s
                              AND display_name = default_label
                            """,
                            (src_display, now, meeting_id, dst_spk),
                        )
                # 只删除已无任何 segment 引用的旧说话人记录（swap 目标除外）。
                orphan_cursor = await connection.execute(
                    f"""
                    SELECT 1 FROM {self._schema}.transcript_segments
                    WHERE meeting_id = %s AND speaker_key = %s LIMIT 1
                    """,
                    (meeting_id, src_spk),
                )
                if await orphan_cursor.fetchone() is None:
                    await connection.execute(
                        f"""
                        DELETE FROM {self._schema}.meeting_speakers
                        WHERE meeting_id = %s AND speaker_key = %s
                        """,
                        (meeting_id, src_spk),
                    )

            content_revision = meeting.content_revision + 1
            transcript_revision = meeting.transcript_revision + 1
            await connection.execute(
                f"""
                UPDATE {self._schema}.meetings
                SET content_revision = %s, transcript_revision = %s, updated_at = %s
                WHERE id = %s
                """,
                (content_revision, transcript_revision, now, meeting_id),
            )
            await self._insert_event(
                connection,
                meeting_id,
                "speaker_remapped",
                {
                    "remapping": remapping,
                    "content_revision": content_revision,
                    "transcript_revision": transcript_revision,
                },
            )
            cursor = await connection.execute(
                f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings WHERE id = %s",
                (meeting_id,),
            )
            updated = await cursor.fetchone()
            if updated is None:
                raise RepositoryUnavailableError("重映射后无法读取会议")
            return _meeting_from_row(updated)

    async def create_minutes(
        self, meeting_id: UUID, *, idempotency_key: str | None
    ) -> MinutesRecord:
        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
            if not idempotency_key or len(idempotency_key) > 200:
                raise ValueError("idempotency_key 无效")
        async with self._connection() as connection:  # noqa: SIM117
            async with connection.transaction():
                meeting = await self._lock_meeting(connection, meeting_id)
                if meeting is None:
                    raise MeetingNotFoundError("会议不存在")
                if meeting.status in {MeetingStatus.RECORDING, MeetingStatus.FINALIZING}:
                    raise MeetingConflictError("会议尚未封存")
                if idempotency_key is not None:
                    existing_cursor = await connection.execute(
                        f"""
                        SELECT {_MINUTES_COLUMNS}
                        FROM {self._schema}.meeting_minutes
                        WHERE meeting_id = %s AND idempotency_key = %s
                        """,
                        (meeting_id, idempotency_key),
                    )
                    existing = await existing_cursor.fetchone()
                    if existing is not None:
                        return _minutes_from_row(existing)
                active_cursor = await connection.execute(
                    f"""
                    SELECT {_MINUTES_COLUMNS}
                    FROM {self._schema}.meeting_minutes
                    WHERE meeting_id = %s AND status IN (%s, %s)
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (
                        meeting_id,
                        MinutesStatus.QUEUED.value,
                        MinutesStatus.GENERATING.value,
                    ),
                )
                active = await active_cursor.fetchone()
                if active is not None:
                    return _minutes_from_row(active)
                version_cursor = await connection.execute(
                    f"""
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM {self._schema}.meeting_minutes
                    WHERE meeting_id = %s
                    """,
                    (meeting_id,),
                )
                version_row = await version_cursor.fetchone()
                version = int(version_row[0]) if version_row is not None else 1
                minutes_id = uuid4()
                now = _utc_now()
                await connection.execute(
                    f"""
                    INSERT INTO {self._schema}.meeting_minutes
                        (id, meeting_id, version, status, source_content_revision, model,
                         prompt_version, idempotency_key, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        minutes_id,
                        meeting_id,
                        version,
                        MinutesStatus.QUEUED.value,
                        meeting.content_revision,
                        self.settings.summary_model,
                        "v4-map-domain-10240",
                        idempotency_key,
                        now,
                        now,
                    ),
                )
                await self._insert_event(
                    connection,
                    meeting_id,
                    "minutes_queued",
                    {"version": version},
                )
                result = await connection.execute(
                    f"SELECT {_MINUTES_COLUMNS} FROM {self._schema}.meeting_minutes WHERE id = %s",
                    (minutes_id,),
                )
                row = await result.fetchone()
                if row is None:
                    raise RepositoryUnavailableError("纪要创建后无法读取")
                return _minutes_from_row(row)

    async def claim_minutes(self, *, max_attempts: int | None = None) -> MinutesJob | None:
        """认领下一条纪要任务；提供 max_attempts 时同时启用有限自动重试。

        可认领范围：
        - queued 新任务；
        - generating 且 lease 过期（worker 崩溃遗留），重试次数不超过 max_attempts；
        - failed 且 attempts 未达 max_attempts（冷却期后自动重试，应对 LM Studio
          短暂不可用等瞬时故障）；达到上限后保持 failed 终态，需手动重建新版本。
        """
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts 必须为正整数")
        async with self._connection() as connection, connection.transaction():
            if max_attempts is None:
                candidate_filter = "status = %s OR (status = %s AND lease_until < now())"
                filter_params: tuple[object, ...] = (
                    MinutesStatus.QUEUED.value,
                    MinutesStatus.GENERATING.value,
                )
            else:
                candidate_filter = (
                    "status = %s"
                    " OR (status = %s AND lease_until < now() AND attempts < %s)"
                    f" OR (status = %s AND attempts < %s"
                    f" AND updated_at < now() - interval '{_FAILED_RETRY_COOLDOWN_SECS} seconds')"
                )
                filter_params = (
                    MinutesStatus.QUEUED.value,
                    MinutesStatus.GENERATING.value,
                    max_attempts,
                    MinutesStatus.FAILED.value,
                    max_attempts,
                )
            result = await connection.execute(
                f"""
                    WITH candidate AS (
                        SELECT id
                        FROM {self._schema}.meeting_minutes
                        WHERE {candidate_filter}
                        ORDER BY created_at, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE {self._schema}.meeting_minutes AS minutes
                    SET status = %s,
                        lease_until = now() + interval '15 minutes',
                        attempts = minutes.attempts + 1,
                        updated_at = now()
                    FROM candidate
                    WHERE minutes.id = candidate.id
                    RETURNING {_MINUTES_COLUMNS_QUALIFIED}
                    """,
                (*filter_params, MinutesStatus.GENERATING.value),
            )
            row = await result.fetchone()
            if row is None:
                return None
            minutes = _minutes_from_row(row)
            meeting_cursor = await connection.execute(
                f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings WHERE id = %s",
                (minutes.meeting_id,),
            )
            meeting_row = await meeting_cursor.fetchone()
            if meeting_row is None:
                raise RepositoryUnavailableError("纪要对应的会议不存在")
            return MinutesJob(minutes=minutes, meeting=_meeting_from_row(meeting_row))

    async def complete_minutes(
        self, minutes_id: UUID, result: MinutesResult
    ) -> MinutesRecord:
        validated_result, markdown = _coerce_minutes_result(result)
        async with self._connection() as connection, connection.transaction():
            now = _utc_now()
            cursor = await connection.execute(
                f"""
                    UPDATE {self._schema}.meeting_minutes
                    SET status = %s, content_json = %s, content_markdown = %s,
                        raw_output = NULL, error_code = NULL, error_message = NULL,
                        generated_at = %s, lease_until = NULL, updated_at = %s
                    WHERE id = %s AND status = %s
                    RETURNING {_MINUTES_COLUMNS}
                    """,
                (
                    MinutesStatus.COMPLETED.value,
                    Jsonb(validated_result.model_dump(mode="json")),
                    markdown,
                    now,
                    now,
                    minutes_id,
                    MinutesStatus.GENERATING.value,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                raise MeetingConflictError("纪要任务不在 generating 状态")
            return _minutes_from_row(row)

    async def fail_minutes(
        self,
        minutes_id: UUID,
        *,
        code: str,
        message: str,
        raw_output: str | None = None,
    ) -> None:
        code = code.strip()
        message = message.strip()
        if not code or len(code) > 128 or len(message) > 2_000:
            raise ValueError("纪要错误信息无效")
        async with self._connection() as connection, connection.transaction():
            cursor = await connection.execute(
                f"""
                    UPDATE {self._schema}.meeting_minutes
                    SET status = %s, error_code = %s, error_message = %s,
                        raw_output = %s, lease_until = NULL, updated_at = %s
                    WHERE id = %s AND status IN (%s, %s)
                    """,
                (
                    MinutesStatus.FAILED.value,
                    code,
                    message,
                    raw_output,
                    _utc_now(),
                    minutes_id,
                    MinutesStatus.GENERATING.value,
                    MinutesStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise MeetingNotFoundError("纪要任务不存在或已完成")

    async def delete_meeting(self, meeting_id: UUID) -> None:
        async with self._connection() as connection, connection.transaction():
            meeting = await self._lock_meeting(connection, meeting_id)
            if meeting is None:
                raise MeetingNotFoundError("会议不存在")
            if meeting.status in {MeetingStatus.RECORDING, MeetingStatus.FINALIZING}:
                raise MeetingConflictError("录制中的会议不能删除")
            cursor = await connection.execute(
                f"DELETE FROM {self._schema}.meetings WHERE id = %s", (meeting_id,)
            )
            if cursor.rowcount != 1:
                raise MeetingNotFoundError("会议不存在")

    async def requeue_generating(self) -> int:
        """录制优先时释放纪要 worker 租约，返回重新排队数量。"""
        async with self._connection() as connection, connection.transaction():
            cursor = await connection.execute(
                f"""
                UPDATE {self._schema}.meeting_minutes
                SET status = %s, lease_until = NULL, updated_at = %s
                WHERE status = %s
                """,
                (
                    MinutesStatus.QUEUED.value,
                    _utc_now(),
                    MinutesStatus.GENERATING.value,
                ),
            )
            return int(cursor.rowcount)

    async def recover_stale(self) -> int:
        """启动时将上次崩溃留下的 recording/finalizing 标为 interrupted。"""
        async with self._connection() as connection, connection.transaction():
            cursor = await connection.execute(
                f"""
                UPDATE {self._schema}.meetings
                SET status = %s, interruption_reason = %s, ended_at = %s, updated_at = %s
                WHERE status IN (%s, %s)
                RETURNING id
                """,
                (
                    MeetingStatus.INTERRUPTED.value,
                    "application_restart",
                    _utc_now(),
                    _utc_now(),
                    MeetingStatus.RECORDING.value,
                    MeetingStatus.FINALIZING.value,
                ),
            )
            rows = await cursor.fetchall()
            for row in rows:
                await self._insert_event(
                    connection,
                    cast(UUID, row[0]),
                    "meeting_interrupted",
                    {"reason": "application_restart"},
                )
            return len(rows)

    async def close(self) -> None:
        """关闭连接池；不会删除任何会议数据。"""
        if self._opened and self._owns_pool:
            await self._pool.close()
            self._opened = False

    async def _lock_meeting(
        self, connection: Any, meeting_id: UUID, *, lock: bool = True
    ) -> MeetingRecord | None:
        suffix = " FOR UPDATE" if lock else ""
        cursor = await connection.execute(
            f"SELECT {_MEETING_COLUMNS} FROM {self._schema}.meetings WHERE id = %s{suffix}",
            (meeting_id,),
        )
        row = await cursor.fetchone()
        return _meeting_from_row(row) if row is not None else None

    @staticmethod
    def _ensure_transition(current: MeetingStatus, target: MeetingStatus) -> None:
        allowed: dict[MeetingStatus, set[MeetingStatus]] = {
            MeetingStatus.RECORDING: {
                MeetingStatus.RECORDING,
                MeetingStatus.FINALIZING,
                MeetingStatus.INTERRUPTED,
                MeetingStatus.STORAGE_ERROR,
            },
            MeetingStatus.FINALIZING: {
                MeetingStatus.FINALIZING,
                MeetingStatus.COMPLETED,
                MeetingStatus.INTERRUPTED,
                MeetingStatus.STORAGE_ERROR,
            },
            MeetingStatus.COMPLETED: {
                MeetingStatus.COMPLETED,
                MeetingStatus.INTERRUPTED,
                MeetingStatus.STORAGE_ERROR,
            },
            MeetingStatus.INTERRUPTED: {MeetingStatus.INTERRUPTED},
            MeetingStatus.STORAGE_ERROR: {MeetingStatus.STORAGE_ERROR},
        }
        if target not in allowed[current]:
            raise MeetingConflictError(f"会议状态不能从 {current.value} 变为 {target.value}")

    async def _upsert_speaker(
        self, connection: Any, meeting_id: UUID, segment: NormalizedSegment
    ) -> None:
        raw_speaker = segment.speaker_key.rsplit(":", 1)[-1]
        default_label = speaker_display_label(segment.speaker_key, raw_speaker)

        cursor = await connection.execute(
            f"""
            SELECT display_name FROM {self._schema}.meeting_speakers
            WHERE meeting_id = %s AND raw_speaker = %s AND display_name != default_label
            ORDER BY updated_at DESC LIMIT 1
            """,
            (meeting_id, raw_speaker),
        )
        existing = await cursor.fetchone()
        display_name = str(existing[0]) if existing and existing[0] else default_label

        await connection.execute(
            f"""
            INSERT INTO {self._schema}.meeting_speakers
                (meeting_id, speaker_key, source_epoch, raw_speaker, default_label, display_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (meeting_id, speaker_key) DO UPDATE SET
                source_epoch = EXCLUDED.source_epoch,
                raw_speaker = EXCLUDED.raw_speaker,
                default_label = EXCLUDED.default_label,
                display_name = CASE
                    WHEN {self._schema}.meeting_speakers.display_name
                         != {self._schema}.meeting_speakers.default_label
                    THEN {self._schema}.meeting_speakers.display_name
                    ELSE EXCLUDED.display_name
                END,
                updated_at = now()
            """,
            (
                meeting_id,
                segment.speaker_key,
                segment.source_epoch,
                raw_speaker,
                default_label,
                display_name,
            ),
        )

    async def _insert_event(
        self, connection: Any, meeting_id: UUID, event_type: str, payload: dict[str, Any]
    ) -> None:
        await connection.execute(
            f"""
            INSERT INTO {self._schema}.meeting_events (id, meeting_id, event_type, payload)
            VALUES (%s, %s, %s, %s)
            """,
            (uuid4(), meeting_id, event_type, Jsonb(payload)),
        )


def _segment_from_detail_row(row: Any) -> NormalizedSegment:
    """_SEGMENT_COLUMNS_DETAIL 行 → NormalizedSegment（含归属证据字段）。"""
    return NormalizedSegment(
        id=cast(UUID, row[0]),
        order=int(row[1]),
        source_epoch=int(row[2]),
        speaker_key=str(row[3]),
        start_ms=int(row[4]),
        end_ms=int(row[5]),
        text=str(row[6]),
        translation=cast(str | None, row[7]),
        detected_language=cast(str | None, row[8]),
        speaker_status=cast(str | None, row[9]),
        timing_quality=cast(str | None, row[10]),
        overlap_ratio=float(row[11]),
        speaker_manual=bool(row[12]),
    )


def _render_minutes_markdown(result: MinutesResult) -> str:
    """Compatibility wrapper around the canonical renderer."""
    return render_minutes_markdown(result)


def _coerce_minutes_result(result: Any) -> tuple[MinutesResult, str]:
    """兼容 summary workstream 的 artifact 包装，同时保持严格结果校验。"""
    if isinstance(result, Mapping):
        content = result.get("content_json")
        markdown = result.get("content_markdown")
    else:
        content = getattr(result, "content_json", None)
        markdown = getattr(result, "content_markdown", None)
    if content is not None:
        validated = MinutesResult.model_validate(content)
        rendered = str(markdown) if markdown is not None else _render_minutes_markdown(validated)
        return validated, rendered
    validated = MinutesResult.model_validate(result)
    return validated, _render_minutes_markdown(validated)
