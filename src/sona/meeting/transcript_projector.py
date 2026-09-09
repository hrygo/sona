"""把正文事实和归属 metadata 投影为可读 DisplayBlock。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from uuid import UUID

from .transcript_models import (
    DisplayBlock,
    ProjectorProfile,
    SpeakerStatus,
    TranscriptAttributionSpan,
    TranscriptItem,
)

_STRONG_ENDINGS = tuple("。！？!?；;．.")


class TranscriptPresentationProjector:
    """无持久化副作用的跨模式转录投影器。"""

    @staticmethod
    def validate_spans(
        item: TranscriptItem, spans: Sequence[TranscriptAttributionSpan]
    ) -> None:
        previous_end = 0
        for span in sorted(spans, key=lambda candidate: candidate.text_start):
            if span.item_id != item.id:
                raise ValueError("attribution span 不属于当前 transcript item")
            if span.text_end > len(item.text):
                raise ValueError("attribution span text range 越界")
            if span.text_start < previous_end:
                raise ValueError("attribution span text range 重叠")
            previous_end = span.text_end

    def project(
        self,
        items: Sequence[TranscriptItem],
        spans: Iterable[TranscriptAttributionSpan] = (),
        *,
        partial_text: str = "",
        partial_speaker_key: str | None = None,
        partial_speaker_name: str | None = None,
        profile: ProjectorProfile | None = None,
    ) -> tuple[DisplayBlock, ...]:
        profile = profile or ProjectorProfile()
        ordered_items = tuple(
            sorted(items, key=lambda item: (item.sequence, item.start_ms, item.id.hex))
        )
        spans_by_item: dict[UUID, list[TranscriptAttributionSpan]] = {}
        for span in spans:
            spans_by_item.setdefault(span.item_id, []).append(span)
        for item in ordered_items:
            self.validate_spans(item, spans_by_item.get(item.id, ()))

        blocks: list[DisplayBlock] = []
        for item in ordered_items:
            item_projection = self._project_item(item, spans_by_item.get(item.id, ()))
            if blocks and self._can_merge(blocks[-1], item, item_projection, profile):
                blocks[-1] = self._merge(blocks[-1], item, item_projection)
            else:
                blocks.append(self._make_block((item,), item_projection))

        if partial_text:
            blocks.append(
                self._partial_block(
                    partial_text,
                    partial_speaker_key=partial_speaker_key,
                    partial_speaker_name=partial_speaker_name,
                )
            )
        return tuple(blocks)

    def _project_item(
        self,
        item: TranscriptItem,
        spans: Sequence[TranscriptAttributionSpan],
    ) -> tuple[str | None, str | None, SpeakerStatus, str, str]:
        if not spans:
            return None, None, "pending", "speaker-neutral", "aligned"
        statuses = {span.speaker_status for span in spans}
        keys = {span.speaker_key for span in spans}
        if "degraded" in statuses:
            status: SpeakerStatus = "degraded"
        elif "off" in statuses:
            status = "off"
        elif len(statuses) == 1:
            status = next(iter(statuses))
        else:
            status = "pending"
        speaker_key = next(iter(keys)) if len(keys) == 1 else None
        names = {span.speaker_name for span in spans if span.speaker_name}
        speaker_name = next(iter(names)) if len(names) == 1 else None
        if status in {"off", "pending"}:
            speaker_key = None
            speaker_name = None
        timing_quality = (
            "unavailable"
            if any(span.timing_quality == "unavailable" for span in spans)
            else "aligned"
        )
        color_key = speaker_key or status
        color_token = f"speaker-{hashlib.sha256(color_key.encode()).hexdigest()[:8]}"
        return speaker_key, speaker_name, status, color_token, timing_quality

    @staticmethod
    def _can_merge(
        previous: DisplayBlock,
        item: TranscriptItem,
        projection: tuple[str | None, str | None, SpeakerStatus, str, str],
        profile: ProjectorProfile,
    ) -> bool:
        if previous.is_partial or not previous.item_ids:
            return False
        if previous.source_ids and item.source_item_id != previous.source_ids[0].split("#", 1)[0]:
            return False
        if previous.speaker_key != projection[0] or previous.speaker_status != projection[2]:
            return False
        if previous.end_ms is None:
            return False
        gap = item.start_ms - previous.end_ms
        if gap > profile.max_gap_ms or gap < 0:
            return False
        if previous.start_ms is None or item.end_ms - previous.start_ms > profile.max_duration_ms:
            return False
        if len(previous.text) + len(item.text) > profile.max_chars:
            return False
        if previous.text.endswith(_STRONG_ENDINGS):
            return False
        # source id embeds source item identity to keep unknown speakers within one item.
        previous_source_item = (
            previous.source_ids[0].split("#", 1)[0] if previous.source_ids else ""
        )
        return previous_source_item == item.source_item_id

    @staticmethod
    def _make_block(
        items: tuple[TranscriptItem, ...],
        projection: tuple[str | None, str | None, SpeakerStatus, str, str],
    ) -> DisplayBlock:
        speaker_key, speaker_name, speaker_status, color_token, timing_quality = projection
        first, last = items[0], items[-1]
        timed = timing_quality == "aligned"
        return DisplayBlock(
            block_id=TranscriptPresentationProjector._block_id(items),
            item_ids=tuple(item.id for item in items),
            source_ids=tuple(f"{item.source_item_id}#{item.source_segment_uid}" for item in items),
            text="".join(item.text for item in items),
            start_ms=first.start_ms if timed else None,
            end_ms=last.end_ms if timed else None,
            speaker_key=speaker_key,
            speaker_name=speaker_name,
            speaker_status=speaker_status,
            speaker_color_token=color_token,
            timing_quality=timing_quality,  # type: ignore[arg-type]
        )

    @staticmethod
    def _merge(
        previous: DisplayBlock,
        item: TranscriptItem,
        projection: tuple[str | None, str | None, SpeakerStatus, str, str],
    ) -> DisplayBlock:
        speaker_key, speaker_name, speaker_status, color_token, timing_quality = projection
        timed = previous.timing_quality == "aligned" and timing_quality == "aligned"
        return DisplayBlock(
            block_id=TranscriptPresentationProjector._block_id_by_ids(
                (*previous.item_ids, item.id)
            ),
            item_ids=(*previous.item_ids, item.id),
            source_ids=(*previous.source_ids, f"{item.source_item_id}#{item.source_segment_uid}"),
            text=f"{previous.text}{item.text}",
            start_ms=previous.start_ms if timed else None,
            end_ms=item.end_ms if timed else None,
            speaker_key=speaker_key,
            speaker_name=speaker_name,
            speaker_status=speaker_status,
            speaker_color_token=color_token,
            timing_quality="aligned" if timed else "unavailable",
        )

    @staticmethod
    def _partial_block(
        text: str,
        *,
        partial_speaker_key: str | None,
        partial_speaker_name: str | None,
    ) -> DisplayBlock:
        status: SpeakerStatus = "anonymous" if partial_speaker_key else "pending"
        color_key = partial_speaker_key or status
        return DisplayBlock(
            block_id="partial",
            text=text,
            speaker_key=partial_speaker_key,
            speaker_name=partial_speaker_name,
            speaker_status=status,
            speaker_color_token=f"speaker-{hashlib.sha256(color_key.encode()).hexdigest()[:8]}",
            timing_quality="unavailable",
            is_partial=True,
        )

    @staticmethod
    def _block_id(items: tuple[TranscriptItem, ...]) -> str:
        return TranscriptPresentationProjector._block_id_by_ids(tuple(item.id for item in items))

    @staticmethod
    def _block_id_by_ids(item_ids: tuple[UUID, ...]) -> str:
        joined_ids = ":".join(str(item_id) for item_id in item_ids)
        digest = hashlib.sha256(joined_ids.encode()).hexdigest()[:20]
        return f"block-{digest}"


__all__ = ["TranscriptPresentationProjector"]
