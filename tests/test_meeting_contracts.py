from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path("contracts/meeting-assistant/v1")
EVENT_SCHEMA_FILES = {
    "meeting_snapshot": "event-meeting-snapshot.schema.json",
    "meeting_state_changed": "event-meeting-state-changed.schema.json",
    "transcript_partial": "event-transcript-partial.schema.json",
    "transcript_reconciled": "event-transcript-reconciled.schema.json",
    "speaker_updated": "event-speaker-updated.schema.json",
    "meeting_title_updated": "event-meeting-title-updated.schema.json",
    "minutes_state_changed": "event-minutes-state-changed.schema.json",
    "health_changed": "event-health-changed.schema.json",
    "transcription_gap": "event-transcription-gap.schema.json",
    "resync_required": "event-resync-required.schema.json",
}
INNER_OS_FIXTURE_FILES = {
    "inner-os-completed.json",
    "inner-os-insufficient.json",
    "inner-os-invalid-focus.json",
}
# speaker_details opt-in 变体：不属于 legacy envelope 事件集，单独校验。
SPEAKER_DETAILS_FIXTURE_FILES = {
    "transcript-reconciled-speaker-details.json",
    "transcript-response-speaker-details.json",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _meeting_event_fixtures() -> list[Path]:
    return sorted(
        path for path in (ROOT / "fixtures").glob("*.json")
        if path.name not in INNER_OS_FIXTURE_FILES
        and path.name not in SPEAKER_DETAILS_FIXTURE_FILES
    )


def _inner_os_fixtures() -> list[Path]:
    return sorted(ROOT / "fixtures" / name for name in INNER_OS_FIXTURE_FILES)


def test_contract_metadata_and_canonical_surface_exist() -> None:
    assert (ROOT / "README.md").is_file()
    assert (ROOT.parent / "CHANGELOG.md").is_file()

    openapi = _load_json(ROOT / "openapi.json")
    assert openapi["paths"]["/api/v1/runtime"]["get"]
    assert openapi["paths"]["/api/v1/meetings/{meeting_id}/inner-os/exchanges"]["get"]
    assert openapi["paths"]["/api/v1/meetings/{meeting_id}/inner-os/exchanges/{exchange_id}"]["put"]

    asyncapi = (ROOT / "asyncapi.yaml").read_text(encoding="utf-8")
    assert "address: /ws/v1/control" in asyncapi
    assert "address: /ws/v1/meetings" in asyncapi
    assert "address: /ws/v1/meetings/{meeting_id}/inner-os" in asyncapi
    assert "start_subtitles" in asyncapi


def test_every_fixture_has_a_strict_event_schema() -> None:
    envelope = _load_json(ROOT / "schemas/event-envelope.schema.json")
    envelope_validator = Draft202012Validator(envelope, format_checker=FormatChecker())

    fixtures = _meeting_event_fixtures()
    assert fixtures
    assert {json.loads(path.read_text(encoding="utf-8"))["type"] for path in fixtures} == set(
        EVENT_SCHEMA_FILES
    )

    for fixture_path in fixtures:
        value = _load_json(fixture_path)
        envelope_validator.validate(value)

        event_type = value["type"]
        schema_path = ROOT / "schemas" / EVENT_SCHEMA_FILES[event_type]
        assert schema_path.is_file(), f"missing schema for {event_type}"
        validator = Draft202012Validator(_load_json(schema_path), format_checker=FormatChecker())
        validator.validate(value)


def test_inner_os_fixtures_conform_to_schema() -> None:
    inner_os_envelope = _load_json(ROOT / "schemas/inner-os-event.schema.json")
    envelope_validator = Draft202012Validator(inner_os_envelope, format_checker=FormatChecker())
    answer_schema = _load_json(ROOT / "schemas/inner-os-answer.schema.json")
    answer_validator = Draft202012Validator(answer_schema, format_checker=FormatChecker())

    fixtures = _inner_os_fixtures()
    assert len(fixtures) == len(INNER_OS_FIXTURE_FILES)

    for fixture_path in fixtures:
        assert fixture_path.is_file(), f"missing fixture {fixture_path}"
        value = _load_json(fixture_path)
        envelope_validator.validate(value)

        if value["type"] == "inner_os_answer_completed":
            answer_validator.validate(value["payload"])
        elif value["type"] == "inner_os_answer_failed":
            assert "error" in value["payload"]
            assert value["payload"]["error"]["code"].startswith("inner_os_")


def test_event_schema_rejects_payload_from_another_event() -> None:
    value = _load_json(ROOT / "fixtures/transcript-partial.json")
    schema = _load_json(ROOT / "schemas/event-transcript-reconciled.schema.json")

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def test_speaker_details_opt_in_schemas_and_fixtures() -> None:
    """新字段只在 opt-in 响应中出现；legacy schema 的 additionalProperties=false 保持不变。"""
    legacy_event_schema = _load_json(
        ROOT / "schemas/event-transcript-reconciled.schema.json"
    )
    detail_event_fixture = _load_json(
        ROOT / "fixtures/transcript-reconciled-speaker-details.json"
    )

    # legacy schema 拒绝归属证据字段
    with pytest.raises(ValidationError):
        Draft202012Validator(legacy_event_schema).validate(detail_event_fixture)

    # opt-in 变体 schema 接受同一 fixture
    variant_event_schema = _load_json(
        ROOT / "schemas/event-transcript-reconciled-speaker-details.schema.json"
    )
    Draft202012Validator(variant_event_schema).validate(detail_event_fixture)

    # response 变体
    response_schema = _load_json(
        ROOT / "schemas/transcript-response-speaker-details.schema.json"
    )
    response_fixture = _load_json(
        ROOT / "fixtures/transcript-response-speaker-details.json"
    )
    Draft202012Validator(response_schema).validate(response_fixture)

    # 缺少 speaker_details 常量的响应不符合 opt-in 契约
    without_flag = {k: v for k, v in response_fixture.items() if k != "speaker_details"}
    with pytest.raises(ValidationError):
        Draft202012Validator(response_schema).validate(without_flag)


def test_backend_presenter_matches_speaker_details_contract() -> None:
    """后端 presenter 与契约 fixture 使用同一测试向量。"""
    from sona.meeting.api import _segment_json
    from sona.meeting.models import NormalizedSegment

    segment = NormalizedSegment(
        id=__import__("uuid").UUID("11111111-1111-4111-8111-111111111111"),
        order=0,
        source_epoch=1,
        speaker_key="speechrail:spk-e2e-1:speaker-source:s1:spk_01",
        start_ms=1000,
        end_ms=2000,
        text="确认发布。",
        detected_language="zh",
        speaker_status="stable",
        timing_quality="aligned",
        overlap_ratio=0.0,
        speaker_manual=False,
    )
    speakers = {
        "speechrail:spk-e2e-1:speaker-source:s1:spk_01": {
            "speaker_key": "speechrail:spk-e2e-1:speaker-source:s1:spk_01",
            "default_label": "说话人 1",
            "display_name": "说话人 1",
        }
    }

    # legacy 视图：原字段集合
    legacy_payload = _segment_json(segment, speakers)
    expected_legacy_keys = {
        "id", "order", "speaker_key", "speaker_name", "start_ms", "end_ms",
        "text", "translation", "detected_language", "source_epoch",
    }
    assert set(legacy_payload) == expected_legacy_keys

    # opt-in 视图：与 fixture 同形
    detail_payload = _segment_json(segment, speakers, detail=True)
    response_fixture = _load_json(
        ROOT / "fixtures/transcript-response-speaker-details.json"
    )
    fixture_segment = response_fixture["segments"][0]
    assert detail_payload == fixture_segment
