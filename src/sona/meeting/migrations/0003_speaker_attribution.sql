-- SPK-E2E-1 讲话人归属加法迁移（S2）。
-- 只新增列/表/索引：旧数据不重写，旧必填列与既有查询保持可读；
-- rollback 不做 DROP COLUMN/TABLE。

-- transcript_segments：不可变正文之上的归属与证据列。
ALTER TABLE __SCHEMA__.transcript_segments
    ADD COLUMN IF NOT EXISTS source_session_id text,
    ADD COLUMN IF NOT EXISTS source_segment_uid text,
    ADD COLUMN IF NOT EXISTS source_item_id text,
    ADD COLUMN IF NOT EXISTS speaker_revision integer NOT NULL DEFAULT 0
        CHECK (speaker_revision >= 0),
    ADD COLUMN IF NOT EXISTS model_speaker_key text,
    ADD COLUMN IF NOT EXISTS speaker_override_key text,
    ADD COLUMN IF NOT EXISTS speaker_status text NOT NULL DEFAULT 'unknown'
        CHECK (speaker_status IN ('unknown', 'tentative', 'stable')),
    ADD COLUMN IF NOT EXISTS speaker_frozen boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS timing_quality text NOT NULL DEFAULT 'unavailable'
        CHECK (timing_quality IN ('aligned', 'unavailable')),
    ADD COLUMN IF NOT EXISTS coverage_ratio double precision NOT NULL DEFAULT 0
        CHECK (coverage_ratio >= 0 AND coverage_ratio <= 1),
    ADD COLUMN IF NOT EXISTS overlap_ratio double precision NOT NULL DEFAULT 0
        CHECK (overlap_ratio >= 0 AND overlap_ratio <= 1),
    ADD COLUMN IF NOT EXISTS speaker_candidates jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_array_length(speaker_candidates) <= 4);

-- 有 source UID 的 segment 在会议内唯一；旧行两列为 NULL，不受影响。
CREATE UNIQUE INDEX IF NOT EXISTS transcript_segments_source_uid_idx
    ON __SCHEMA__.transcript_segments (meeting_id, source_session_id, source_segment_uid)
    WHERE source_session_id IS NOT NULL AND source_segment_uid IS NOT NULL;

-- meetings：分人生命周期与 legacy 共存；持久终态仍是 completed/interrupted。
ALTER TABLE __SCHEMA__.meetings
    ADD COLUMN IF NOT EXISTS diarization_status text NOT NULL DEFAULT 'legacy'
        CHECK (diarization_status IN ('legacy', 'active', 'complete', 'degraded')),
    ADD COLUMN IF NOT EXISTS diarization_reason text
        CHECK (diarization_reason IS NULL OR char_length(diarization_reason) <= 128);

-- 每个 source epoch 的时钟与水位记录（Rail session 样本域）。
CREATE TABLE IF NOT EXISTS __SCHEMA__.meeting_transcription_sources (
    meeting_id uuid NOT NULL REFERENCES __SCHEMA__.meetings(id) ON DELETE CASCADE,
    source_epoch integer NOT NULL CHECK (source_epoch >= 0),
    session_id text NOT NULL CHECK (char_length(session_id) BETWEEN 1 AND 128),
    meeting_start_sample bigint NOT NULL DEFAULT 0 CHECK (meeting_start_sample >= 0),
    last_committed_meeting_sample bigint NOT NULL DEFAULT 0
        CHECK (last_committed_meeting_sample >= 0),
    stable_through_sample bigint NOT NULL DEFAULT 0 CHECK (stable_through_sample >= 0),
    last_update_sequence bigint NOT NULL DEFAULT 0 CHECK (last_update_sequence >= 0),
    group_generation text CHECK (group_generation IS NULL OR char_length(group_generation) <= 128),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (meeting_id, source_epoch),
    UNIQUE (meeting_id, session_id)
);

-- (session, 匿名标签) → 会议内不透明应用身份的映射。
CREATE TABLE IF NOT EXISTS __SCHEMA__.meeting_speaker_sources (
    meeting_id uuid NOT NULL REFERENCES __SCHEMA__.meetings(id) ON DELETE CASCADE,
    session_id text NOT NULL CHECK (char_length(session_id) BETWEEN 1 AND 128),
    source_speaker text NOT NULL CHECK (char_length(source_speaker) BETWEEN 1 AND 64),
    group_generation text CHECK (group_generation IS NULL OR char_length(group_generation) <= 128),
    application_speaker_key text NOT NULL
        CHECK (char_length(application_speaker_key) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (meeting_id, session_id, source_speaker)
);

-- 源事件去重凭证：同 event 幂等，同 ID 不同内容哈希 = 协议冲突。
CREATE TABLE IF NOT EXISTS __SCHEMA__.meeting_source_events (
    meeting_id uuid NOT NULL REFERENCES __SCHEMA__.meetings(id) ON DELETE CASCADE,
    session_id text NOT NULL CHECK (char_length(session_id) BETWEEN 1 AND 128),
    event_id text NOT NULL CHECK (char_length(event_id) BETWEEN 1 AND 128),
    event_kind text NOT NULL CHECK (event_kind IN ('completed', 'patch', 'finalize')),
    canonical_payload_hash text NOT NULL CHECK (char_length(canonical_payload_hash) = 64),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (meeting_id, session_id, event_id)
);

CREATE INDEX IF NOT EXISTS transcript_segments_source_session_idx
    ON __SCHEMA__.transcript_segments (meeting_id, source_session_id);
