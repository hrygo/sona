"""字幕窗口到统一可读块的投影契约。"""

from sona.asr.models import ASRSegment, ASRWindow
from sona.asr.presenters import legacy_subtitle_payload


def _segment(
    text: str,
    *,
    uid: str,
    speaker: str = "unknown",
    start_ms: int = 0,
    end_ms: int = 1_000,
) -> ASRSegment:
    return ASRSegment(
        order=0,
        source_epoch=1,
        speaker_key=speaker,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        source_uid=uid,
        source_session_id="subtitle-session-1",
    )


def test_subtitle_payload_uses_display_blocks_and_keeps_one_partial_bottom_block() -> None:
    payload = legacy_subtitle_payload(
        ASRWindow(
            source_epoch=1,
            source_session_id="subtitle-session-1",
            partial="正在识别",
            partial_speaker_key="unknown",
            diarization_status="active",
            segments=(
                _segment("第一句", uid="seg-1"),
                _segment("第二句", uid="seg-2", start_ms=1_100, end_ms=2_000),
            ),
        )
    )

    blocks = payload["display_blocks"]
    assert len(blocks) == 2
    assert blocks[0]["text"] == "第一句第二句"
    assert blocks[0]["speaker_status"] == "pending"
    assert blocks[0]["is_partial"] is False
    assert blocks[1]["text"] == "正在识别"
    assert blocks[1]["is_partial"] is True
    assert blocks[1]["speaker_status"] == "pending"
    assert blocks[1]["start_ms"] is None
    assert blocks[1]["end_ms"] is None


def test_subtitle_speaker_revision_keeps_item_identity_and_body_text() -> None:
    initial = legacy_subtitle_payload(
        ASRWindow(
            source_epoch=1,
            source_session_id="subtitle-session-1",
            diarization_status="active",
            segments=(_segment("不可变正文", uid="seg-1"),),
        )
    )
    revised = legacy_subtitle_payload(
        ASRWindow(
            source_epoch=1,
            source_session_id="subtitle-session-1",
            diarization_status="active",
            segments=(_segment("不可变正文", uid="seg-1", speaker="speaker-2"),),
        )
    )

    before = initial["display_blocks"][0]
    after = revised["display_blocks"][0]
    assert after["text"] == before["text"]
    assert after["item_ids"] == before["item_ids"]
    assert after["source_ids"] == before["source_ids"]
    assert before["speaker_status"] == "pending"
    assert after["speaker_status"] == "anonymous"
    assert after["speaker_key"] == "speaker-2"


def test_subtitle_partial_delta_reuses_one_stable_block_id() -> None:
    first = legacy_subtitle_payload(
        ASRWindow(source_epoch=1, partial="正在", partial_speaker_key="speaker-1")
    )
    second = legacy_subtitle_payload(
        ASRWindow(source_epoch=1, partial="正在识别", partial_speaker_key="speaker-1")
    )

    assert first["display_blocks"][0]["block_id"] == "partial"
    assert second["display_blocks"][0]["block_id"] == "partial"
    assert first["display_blocks"][0]["text"] != second["display_blocks"][0]["text"]


def test_subtitle_off_and_degraded_statuses_are_semantic() -> None:
    off = legacy_subtitle_payload(
        ASRWindow(
            source_epoch=1,
            diarization_status="off",
            segments=(_segment("未启用分人", uid="off-1", speaker="speaker-1"),),
        )
    )
    degraded = legacy_subtitle_payload(
        ASRWindow(
            source_epoch=1,
            diarization_status="degraded",
            diarization_reason="服务不可用",
            segments=(_segment("分人降级", uid="degraded-1", speaker="speaker-1"),),
        )
    )

    assert off["display_blocks"][0]["speaker_status"] == "off"
    assert off["display_blocks"][0]["speaker_key"] is None
    assert degraded["display_blocks"][0]["speaker_status"] == "degraded"
    assert degraded["display_blocks"][0]["speaker_key"] is None


def test_subtitle_unavailable_timing_does_not_invent_timestamps() -> None:
    segment = ASRSegment(
        order=0,
        source_epoch=1,
        speaker_key="unknown",
        start_ms=100,
        end_ms=900,
        text="无时间正文",
        source_uid="seg-1",
        source_session_id="subtitle-session-1",
        timing_quality="unavailable",
    )
    payload = legacy_subtitle_payload(
        ASRWindow(
            source_epoch=1,
            source_session_id="subtitle-session-1",
            segments=(segment,),
        )
    )

    block = payload["display_blocks"][0]
    assert block["timing_quality"] == "unavailable"
    assert block["start_ms"] is None
    assert block["end_ms"] is None
