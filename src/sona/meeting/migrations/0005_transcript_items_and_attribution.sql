-- Issue #14：正文事实与可修订 attribution span 分离。
-- 旧 transcript_segments 保留用于迁移窗口只读兼容；本 migration 不删除或重写旧数据。

CREATE TABLE IF NOT EXISTS __SCHEMA__.transcript_items (
    id uuid PRIMARY KEY,
    meeting_id uuid NOT NULL REFERENCES __SCHEMA__.meetings(id) ON DELETE CASCADE,
    source_session_id text NOT NULL CHECK (char_length(source_session_id) BETWEEN 1 AND 128),
    source_epoch integer NOT NULL CHECK (source_epoch >= 0),
    source_item_id text NOT NULL CHECK (char_length(source_item_id) BETWEEN 1 AND 128),
    source_segment_uid text NOT NULL CHECK (char_length(source_segment_uid) BETWEEN 1 AND 128),
    sequence bigint NOT NULL CHECK (sequence >= 0),
    start_ms bigint NOT NULL CHECK (start_ms >= 0),
    end_ms bigint NOT NULL CHECK (end_ms >= start_ms),
    text text NOT NULL CHECK (char_length(text) > 0),
    language text NOT NULL CHECK (char_length(language) BETWEEN 1 AND 32),
    status text NOT NULL DEFAULT 'completed' CHECK (status = 'completed'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (meeting_id, source_session_id, source_item_id),
    UNIQUE (meeting_id, source_session_id, source_segment_uid)
);

CREATE TABLE IF NOT EXISTS __SCHEMA__.transcript_attribution_spans (
    id uuid PRIMARY KEY,
    item_id uuid NOT NULL REFERENCES __SCHEMA__.transcript_items(id) ON DELETE CASCADE,
    source_session_id text NOT NULL CHECK (char_length(source_session_id) BETWEEN 1 AND 128),
    source_segment_uid text NOT NULL CHECK (char_length(source_segment_uid) BETWEEN 1 AND 128),
    text_start integer NOT NULL CHECK (text_start >= 0),
    text_end integer NOT NULL CHECK (text_end > text_start),
    audio_start_ms bigint NOT NULL CHECK (audio_start_ms >= 0),
    audio_end_ms bigint NOT NULL CHECK (audio_end_ms >= audio_start_ms),
    timing_quality text NOT NULL CHECK (timing_quality IN ('aligned', 'unavailable')),
    speaker_key text CHECK (speaker_key IS NULL OR char_length(speaker_key) BETWEEN 1 AND 200),
    speaker_status text NOT NULL CHECK (
        speaker_status IN ('identified', 'anonymous', 'pending', 'off', 'degraded')
    ),
    speaker_name text CHECK (speaker_name IS NULL OR char_length(speaker_name) BETWEEN 1 AND 200),
    speaker_confidence double precision
        CHECK (speaker_confidence IS NULL OR (speaker_confidence >= 0 AND speaker_confidence <= 1)),
    speaker_revision integer NOT NULL DEFAULT 0 CHECK (speaker_revision >= 0),
    manually_corrected boolean NOT NULL DEFAULT false,
    speaker_override_key text
        CHECK (speaker_override_key IS NULL OR char_length(speaker_override_key) BETWEEN 1 AND 200),
    model_speaker_key text
        CHECK (model_speaker_key IS NULL OR char_length(model_speaker_key) BETWEEN 1 AND 200),
    candidates jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(candidates) = 'array' AND jsonb_array_length(candidates) <= 8),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (item_id, source_segment_uid)
);

CREATE TABLE IF NOT EXISTS __SCHEMA__.transcript_attribution_revisions (
    id uuid PRIMARY KEY,
    span_id uuid NOT NULL REFERENCES __SCHEMA__.transcript_attribution_spans(id) ON DELETE CASCADE,
    revision integer NOT NULL CHECK (revision >= 1),
    speaker_key text CHECK (speaker_key IS NULL OR char_length(speaker_key) BETWEEN 1 AND 200),
    speaker_status text NOT NULL CHECK (
        speaker_status IN ('identified', 'anonymous', 'pending', 'off', 'degraded')
    ),
    manually_corrected boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (span_id, revision)
);

CREATE INDEX IF NOT EXISTS transcript_items_meeting_sequence_idx
    ON __SCHEMA__.transcript_items (meeting_id, sequence, id);
CREATE INDEX IF NOT EXISTS transcript_items_meeting_time_idx
    ON __SCHEMA__.transcript_items (meeting_id, start_ms, id);
CREATE INDEX IF NOT EXISTS transcript_attribution_spans_meeting_uid_idx
    ON __SCHEMA__.transcript_attribution_spans (source_session_id, source_segment_uid);
CREATE INDEX IF NOT EXISTS transcript_attribution_spans_item_range_idx
    ON __SCHEMA__.transcript_attribution_spans (item_id, text_start, text_end);
CREATE INDEX IF NOT EXISTS transcript_attribution_revisions_span_revision_idx
    ON __SCHEMA__.transcript_attribution_revisions (span_id, revision);

CREATE OR REPLACE FUNCTION __SCHEMA__.transcript_items_immutable_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF ROW(
        NEW.meeting_id, NEW.source_session_id, NEW.source_epoch,
        NEW.source_item_id, NEW.source_segment_uid, NEW.sequence,
        NEW.start_ms, NEW.end_ms, NEW.text, NEW.language, NEW.status,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.meeting_id, OLD.source_session_id, OLD.source_epoch,
        OLD.source_item_id, OLD.source_segment_uid, OLD.sequence,
        OLD.start_ms, OLD.end_ms, OLD.text, OLD.language, OLD.status,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION 'transcript_items immutable facts cannot be changed';
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS transcript_items_immutable_trigger
    ON __SCHEMA__.transcript_items;
CREATE TRIGGER transcript_items_immutable_trigger
    BEFORE UPDATE ON __SCHEMA__.transcript_items
    FOR EACH ROW
    EXECUTE FUNCTION __SCHEMA__.transcript_items_immutable_guard();
