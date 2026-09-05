"""测试会议说话人时序平滑与短片段噪声滤波（DiarizationSmoother）。"""

from uuid import uuid4

from sona.meeting.diarization_smoother import DiarizationSmoother
from sona.meeting.models import NormalizedSegment, TranscriptWindow


def _make_segment(
    order: int,
    speaker: str,
    start_ms: int,
    end_ms: int,
    text: str,
    epoch: int = 1,
) -> NormalizedSegment:
    return NormalizedSegment(
        id=uuid4(),
        order=order,
        source_epoch=epoch,
        speaker_key=speaker,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
    )


def test_diarization_smoother_disabled() -> None:
    smoother = DiarizationSmoother(enabled=False)
    seg1 = _make_segment(0, "speaker:s0", 0, 100, "...")
    window = TranscriptWindow(source_epoch=1, partial="...", segments=(seg1,))
    result = smoother.smooth_window(window)
    assert result == window


def test_diarization_smoother_preserves_partial_speaker_identity() -> None:
    window = TranscriptWindow(
        source_epoch=1,
        partial="正在说",
        partial_speaker_key="epoch:1:speaker:1",
        partial_speaker_name="主持人",
        segments=(_make_segment(0, "speaker:s0", 0, 1000, "已确认"),),
    )

    smoothed = DiarizationSmoother().smooth_window(window)

    assert smoothed.partial_speaker_key == "epoch:1:speaker:1"
    assert smoothed.partial_speaker_name == "主持人"


def test_diarization_smoother_filter_short_noise() -> None:
    smoother = DiarizationSmoother(min_duration_ms=350)
    # seg1: 100ms 纯符号杂音 -> 过滤
    seg1 = _make_segment(0, "speaker:s0", 0, 100, "......")
    # seg2: 100ms 有实质文本 -> 保留
    seg2 = _make_segment(1, "speaker:s0", 200, 300, "好")
    # seg3: 500ms 正常句子 -> 保留
    seg3 = _make_segment(2, "speaker:s1", 500, 1000, "今天开会。")

    window = TranscriptWindow(source_epoch=1, partial="", segments=(seg1, seg2, seg3))
    smoothed = smoother.smooth_window(window)

    assert len(smoothed.segments) == 2
    assert smoothed.segments[0].text == "好"
    assert smoothed.segments[0].order == 0
    assert smoothed.segments[1].text == "今天开会。"
    assert smoothed.segments[1].order == 1


def test_diarization_smoother_keeps_meaningful_short_interjection() -> None:
    smoother = DiarizationSmoother(min_duration_ms=350, hangover_gap_ms=1000)
    # A -> B -> A 模式，中间 B 只有 200ms 但有实质文本（真实短插话）：
    # 不得凭时长把 B 改成 A，文字与 source speaker 都必须保留。
    seg_a1 = _make_segment(0, "speaker:s0", 0, 1000, "我们先看一下第一个方案")
    seg_b = _make_segment(1, "speaker:s1", 1100, 1300, "嗯对")
    seg_a2 = _make_segment(2, "speaker:s0", 1400, 2500, "这个方案的具体细节。")

    window = TranscriptWindow(source_epoch=1, segments=(seg_a1, seg_b, seg_a2))
    smoothed = smoother.smooth_window(window)

    assert len(smoothed.segments) == 3
    interjection = smoothed.segments[1]
    assert interjection.id == seg_b.id
    assert interjection.speaker_key == "speaker:s1"
    assert interjection.text == "嗯对"
    assert interjection.start_ms == 1100
    assert interjection.end_ms == 1300


def test_diarization_smoother_still_corrects_meaningless_flicker() -> None:
    smoother = DiarizationSmoother(min_duration_ms=350, hangover_gap_ms=1000)
    # 纯符号/无实质内容的 A-B-A 闪烁仍按原语义纠偏并合并。
    seg_a1 = _make_segment(0, "speaker:s0", 0, 1000, "我们先看一下第一个方案")
    seg_b = _make_segment(1, "speaker:s1", 1100, 1300, "。。。")
    seg_a2 = _make_segment(2, "speaker:s0", 1400, 2500, "这个方案的具体细节。")

    window = TranscriptWindow(source_epoch=1, segments=(seg_a1, seg_b, seg_a2))
    smoothed = smoother.smooth_window(window)

    assert len(smoothed.segments) == 1
    assert smoothed.segments[0].speaker_key == "speaker:s0"


def test_diarization_smoother_keeps_unknown_interjection() -> None:
    smoother = DiarizationSmoother(min_duration_ms=350, hangover_gap_ms=1000)
    # unknown（speaker:0）短片段不得被折叠进邻近真人，也不得把真人改写成 unknown。
    seg_a1 = _make_segment(0, "speaker:s0", 0, 1000, "我们先看一下第一个方案")
    seg_unknown = _make_segment(1, "epoch:1:speaker:0", 1100, 1300, "嗯")
    seg_a2 = _make_segment(2, "speaker:s0", 1400, 2500, "这个方案的具体细节。")

    window = TranscriptWindow(source_epoch=1, segments=(seg_a1, seg_unknown, seg_a2))
    smoothed = smoother.smooth_window(window)

    kept = next(seg for seg in smoothed.segments if seg.id == seg_unknown.id)
    assert kept.speaker_key == "epoch:1:speaker:0"
    assert kept.text == "嗯"


def test_diarization_smoother_does_not_rewrite_named_into_unknown() -> None:
    smoother = DiarizationSmoother(min_duration_ms=350, hangover_gap_ms=1000)
    # 两侧是 unknown、中间是有名短片段：不得把真人身份抹成 unknown。
    seg_u1 = _make_segment(0, "epoch:1:speaker:0", 0, 1000, "（杂音）")
    seg_named = _make_segment(1, "speaker:s1", 1100, 1300, "对")
    seg_u2 = _make_segment(2, "epoch:1:speaker:0", 1400, 2500, "（杂音）")

    window = TranscriptWindow(source_epoch=1, segments=(seg_u1, seg_named, seg_u2))
    smoothed = smoother.smooth_window(window)

    kept = next(seg for seg in smoothed.segments if seg.id == seg_named.id)
    assert kept.speaker_key == "speaker:s1"


def test_diarization_smoother_same_speaker_merging() -> None:
    smoother = DiarizationSmoother(hangover_gap_ms=800)
    # 两个同说话人段落，间隙 200ms <= 800ms
    seg1 = _make_segment(0, "speaker:s0", 0, 1000, "大家请看屏幕，")
    seg2 = _make_segment(1, "speaker:s0", 1200, 2000, "这是第一版设计。")
    # 间隙 1200ms > 800ms，不应合并
    seg3 = _make_segment(2, "speaker:s0", 3200, 4000, "接下来是第二部分。")

    window = TranscriptWindow(source_epoch=1, segments=(seg1, seg2, seg3))
    smoothed = smoother.smooth_window(window)

    assert len(smoothed.segments) == 2
    assert smoothed.segments[0].text == "大家请看屏幕，这是第一版设计。"
    assert smoothed.segments[0].start_ms == 0
    assert smoothed.segments[0].end_ms == 2000
    assert smoothed.segments[0].order == 0

    assert smoothed.segments[1].text == "接下来是第二部分。"
    assert smoothed.segments[1].start_ms == 3200
    assert smoothed.segments[1].order == 1


def test_diarization_smoother_latin_text_spacing() -> None:
    smoother = DiarizationSmoother(hangover_gap_ms=500)
    seg1 = _make_segment(0, "speaker:s0", 0, 1000, "Hello")
    seg2 = _make_segment(1, "speaker:s0", 1100, 2000, "world")

    window = TranscriptWindow(source_epoch=1, segments=(seg1, seg2))
    smoothed = smoother.smooth_window(window)

    assert len(smoothed.segments) == 1
    assert smoothed.segments[0].text == "Hello world"


def test_diarization_smoother_abba_flicker_correction() -> None:
    smoother = DiarizationSmoother(min_duration_ms=350, hangover_gap_ms=1000)
    # A -> B -> B -> A 模式，中间两段 B 共 300ms，但都有实质文本：
    # 真实短插话必须保留 speaker 与文字，不得合并。
    seg_a1 = _make_segment(0, "speaker:s0", 0, 1000, "第一阶段的")
    seg_b1 = _make_segment(1, "speaker:s1", 1050, 1200, "核心")
    seg_b2 = _make_segment(2, "speaker:s1", 1210, 1350, "工作是")
    seg_a2 = _make_segment(3, "speaker:s0", 1400, 2500, "治理说话人漂移。")

    window = TranscriptWindow(source_epoch=1, segments=(seg_a1, seg_b1, seg_b2, seg_a2))
    smoothed = smoother.smooth_window(window)

    # 两段 B 同说话人相邻，阅读层允许合并，但 speaker 保持 s1、文字与时间不丢。
    assert len(smoothed.segments) == 3
    assert [seg.speaker_key for seg in smoothed.segments] == [
        "speaker:s0",
        "speaker:s1",
        "speaker:s0",
    ]
    interjection = smoothed.segments[1]
    assert interjection.id == seg_b1.id
    assert interjection.text == "核心工作是"
    assert interjection.start_ms == 1050
    assert interjection.end_ms == 1350


def test_diarization_smoother_cross_epoch_merging() -> None:
    smoother = DiarizationSmoother(hangover_gap_ms=800)
    # 两个同说话人段落跨 source_epoch，间隙 100ms
    seg1 = _make_segment(0, "epoch:0:speaker:0", 0, 1000, "第一句在断线前，", epoch=0)
    seg2 = _make_segment(1, "epoch:0:speaker:0", 1100, 2000, "第二句在重连后。", epoch=1)

    window = TranscriptWindow(source_epoch=1, segments=(seg1, seg2))
    smoothed = smoother.smooth_window(window)

    assert len(smoothed.segments) == 1
    assert smoothed.segments[0].text == "第一句在断线前，第二句在重连后。"
    assert smoothed.segments[0].start_ms == 0
    assert smoothed.segments[0].end_ms == 2000

