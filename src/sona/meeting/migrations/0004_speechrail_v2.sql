-- SpeechRail v2.0.0 cleanup: the active schema has one namespaced opt-in path.
-- Historical rows remain readable, but obsolete group metadata is no longer
-- writable or part of the current identity model.

ALTER TABLE __SCHEMA__.meetings
    ALTER COLUMN diarization_status SET DEFAULT 'off';

ALTER TABLE __SCHEMA__.meetings
    DROP CONSTRAINT IF EXISTS meetings_diarization_status_check;

ALTER TABLE __SCHEMA__.meetings
    ADD CONSTRAINT meetings_diarization_status_check
    CHECK (diarization_status IN ('off', 'legacy', 'active', 'complete', 'degraded'));

ALTER TABLE __SCHEMA__.meeting_transcription_sources
    DROP COLUMN IF EXISTS group_generation;

ALTER TABLE __SCHEMA__.meeting_speaker_sources
    DROP COLUMN IF EXISTS group_generation;
