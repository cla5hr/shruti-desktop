"""Diarization step: assignment logic (pure) + full chain with fake backends."""

import json

import pytest
from helpers import make_meeting_with_upload, make_wav_bytes
from sqlalchemy import select

from shruti_core import jobs
from shruti_core.models import Speaker, TranscriptSegment
from shruti_core.settings import get_settings
from shruti_worker.main import run_until_idle
from shruti_worker.pipeline.diarize import BACKENDS as DIARIZE_BACKENDS
from shruti_worker.pipeline.diarize import (
    Turn,
    _min_speech_s,
    assign_speakers,
    drop_minor_speakers,
    resegment_by_speaker,
)


def T(s, e, label):  # noqa: N802 - terse test helper
    return Turn(start_ms=s, end_ms=e, label=label)


class TestAssignSpeakers:
    def test_max_overlap_wins(self):
        turns = [T(0, 5000, "A"), T(5000, 10000, "B")]
        # segment 4000-7000 overlaps A for 1s, B for 2s
        assert assign_speakers([(4000, 7000)], turns) == ["B"]

    def test_no_overlap_is_none(self):
        assert assign_speakers([(20000, 25000)], [T(0, 5000, "A")]) == [None]

    def test_exact_containment(self):
        assert assign_speakers([(1000, 2000)], [T(0, 5000, "A")]) == ["A"]

    def test_empty_turns(self):
        assert assign_speakers([(0, 1000)], []) == [None]

    def test_fixture_alignment(self, fixtures_dir):
        turns = [
            T(t["start_ms"], t["end_ms"], t["label"])
            for t in json.loads((fixtures_dir / "sample-diarization.json").read_text())["turns"]
        ]
        asr = json.loads((fixtures_dir / "sample-asr.json").read_text())
        segs = [(s["start_ms"], s["end_ms"]) for s in asr["segments"]]
        assert assign_speakers(segs, turns) == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]


class TestResegmentBySpeaker:
    def _words(self, *triples):
        return [{"w": w, "s": s, "e": e} for (w, s, e) in triples]

    def test_splits_a_segment_at_the_speaker_boundary(self):
        # one Whisper segment that straddles a speaker change mid-sentence
        turns = [T(0, 2000, "SPEAKER_00"), T(2000, 5000, "SPEAKER_01")]
        segments = [
            {
                "start_ms": 0,
                "end_ms": 4800,
                "text": "hello there yes speaking",
                "words": self._words(
                    ("hello", 0, 900),
                    ("there", 1000, 1900),
                    ("yes", 2100, 2900),
                    ("speaking", 3000, 4800),
                ),
            }
        ]
        out = resegment_by_speaker(segments, turns)
        assert len(out) == 2
        assert out[0]["speaker_label"] == "SPEAKER_00"
        assert out[0]["text"] == "hello there"
        assert out[1]["speaker_label"] == "SPEAKER_01"
        assert out[1]["text"] == "yes speaking"
        # boundary lands between the words, not on the segment edge
        assert out[0]["end_ms"] == 1900
        assert out[1]["start_ms"] == 2100

    def test_single_speaker_segment_stays_whole(self):
        turns = [T(0, 5000, "SPEAKER_00")]
        segments = [
            {
                "start_ms": 0,
                "end_ms": 2000,
                "text": "all mine",
                "words": self._words(("all", 0, 900), ("mine", 1000, 1900)),
            }
        ]
        out = resegment_by_speaker(segments, turns)
        assert len(out) == 1
        assert out[0]["speaker_label"] == "SPEAKER_00"
        assert out[0]["text"] == "all mine"

    def test_falls_back_to_segment_level_without_words(self):
        turns = [T(0, 3000, "SPEAKER_00"), T(3000, 6000, "SPEAKER_01")]
        segments = [
            {"start_ms": 0, "end_ms": 2500, "text": "first", "words": None},
            {"start_ms": 3500, "end_ms": 5500, "text": "second", "words": []},
        ]
        out = resegment_by_speaker(segments, turns)
        assert [s["speaker_label"] for s in out] == ["SPEAKER_00", "SPEAKER_01"]

    def test_words_in_turn_gaps_inherit_surrounding_speaker(self):
        # "um" falls in the gap between two turns → must inherit, not become a blank segment
        turns = [T(0, 1000, "SPEAKER_00"), T(2000, 5000, "SPEAKER_01")]
        segments = [
            {
                "start_ms": 0,
                "end_ms": 3900,
                "text": "hi um there yes",
                "words": self._words(
                    ("hi", 0, 900),
                    ("um", 1200, 1600),  # 1200-1600 is in the gap
                    ("there", 2100, 2900),
                    ("yes", 3000, 3900),
                ),
            }
        ]
        out = resegment_by_speaker(segments, turns)
        assert [s["speaker_label"] for s in out] == ["SPEAKER_00", "SPEAKER_01"]
        assert out[0]["text"] == "hi um"  # gap word absorbed into SPEAKER_00
        assert out[1]["text"] == "there yes"
        assert all(s["speaker_label"] is not None for s in out)  # no blank fragments

    def test_same_speaker_across_a_long_pause_splits(self):
        turns = [T(0, 10000, "SPEAKER_00")]
        segments = [
            {
                "start_ms": 0,
                "end_ms": 9000,
                "text": "a b",
                "words": self._words(("a", 0, 500), ("b", 8000, 9000)),  # 7.5s gap
            }
        ]
        out = resegment_by_speaker(segments, turns, gap_ms=1200)
        assert len(out) == 2  # same speaker but a real pause → separate utterances


class TestDropMinorSpeakers:
    def test_junk_singletons_are_dropped(self):
        # one real conversation + scattered sub-second junk clusters
        turns = [
            T(0, 60_000, "A"),
            T(60_000, 110_000, "B"),
            T(110_500, 111_000, "J1"),  # 0.5s notification sound
            T(111_200, 111_500, "J2"),  # 0.3s keyboard
            T(112_000, 160_000, "A"),
        ]
        kept = drop_minor_speakers(turns, min_speech_s=10.0)
        assert {t.label for t in kept} == {"A", "B"}

    def test_short_but_real_speaker_survives_scaled_floor(self):
        # 23s clip where B spoke only 6s — the scaled floor keeps them
        turns = [T(0, 15_000, "A"), T(16_000, 22_000, "B")]
        floor = _min_speech_s(23.0)
        assert floor == 2.0
        assert {t.label for t in drop_minor_speakers(turns, floor)} == {"A", "B"}

    def test_all_tiny_keeps_the_biggest(self):
        turns = [T(0, 900, "A"), T(1000, 3000, "B")]
        kept = drop_minor_speakers(turns, min_speech_s=10.0)
        assert {t.label for t in kept} == {"B"}

    def test_floor_scales_and_clamps(self):
        assert _min_speech_s(10) == 2.0  # short clip: 2s floor
        assert _min_speech_s(100) == 5.0  # 5% in the middle band
        assert _min_speech_s(3600) == 10.0  # long meeting: capped at 10s


@pytest.fixture()
def fake_diarize(monkeypatch, fixtures_dir):
    data = json.loads((fixtures_dir / "sample-diarization.json").read_text())
    turns = [Turn(t["start_ms"], t["end_ms"], t["label"]) for t in data["turns"]]

    class FakeBackend:
        name = "fake"

        def diarize(self, wav_path, settings):
            return turns

    monkeypatch.setitem(DIARIZE_BACKENDS, "fake", FakeBackend())
    monkeypatch.setenv("DIARIZE_BACKEND", "fake")
    monkeypatch.setenv("DIARIZE_ENABLED", "1")
    get_settings.cache_clear()
    yield turns
    get_settings.cache_clear()


def test_full_chain_with_diarization(db, tmp_storage, fake_asr, fake_diarize):
    """upload -> extract -> waveform -> asr -> diarize -> ready with speakers."""
    meeting, rec = make_meeting_with_upload(db, make_wav_bytes(1.0))
    jobs.enqueue(
        db,
        "extract_audio",
        queue="io",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
    )

    processed = run_until_idle(["io", "gpu"])
    assert processed == 5  # extract, waveform, asr, diarize, summarize

    db.refresh(meeting)
    assert meeting.status == "ready"

    speakers = db.scalars(
        select(Speaker).where(Speaker.meeting_id == meeting.id).order_by(Speaker.source_label)
    ).all()
    assert [s.source_label for s in speakers] == ["SPEAKER_00", "SPEAKER_01"]

    segments = db.scalars(select(TranscriptSegment).order_by(TranscriptSegment.idx)).all()
    by_label = [s.speaker_label for s in segments]
    assert by_label == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    assert segments[0].speaker_id == speakers[0].id
    assert segments[1].speaker_id == speakers[1].id


def test_diarize_rerun_is_idempotent(db, tmp_storage, fake_asr, fake_diarize):
    meeting, rec = make_meeting_with_upload(db, make_wav_bytes(1.0))
    jobs.enqueue(
        db,
        "extract_audio",
        queue="io",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
    )
    run_until_idle(["io", "gpu"])

    # re-run diarize for the same transcript (crash-retry scenario)
    from shruti_core.models import Transcript

    transcript = db.scalars(select(Transcript).where(Transcript.is_active)).one()
    jobs.enqueue(
        db,
        "diarize",
        queue="gpu",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id), "transcript_id": str(transcript.id)},
    )
    run_until_idle(["gpu"])

    speakers = db.scalars(select(Speaker).where(Speaker.meeting_id == meeting.id)).all()
    assert len(speakers) == 2  # not duplicated
    db.refresh(meeting)
    assert meeting.status == "ready"


def test_meeting_stays_processing_until_diarize(db, tmp_storage, fake_asr, fake_diarize):
    meeting, rec = make_meeting_with_upload(db, make_wav_bytes(1.0))
    jobs.enqueue(
        db,
        "extract_audio",
        queue="io",
        meeting_id=meeting.id,
        payload={"recording_id": str(rec.id)},
    )
    # run only io queue: extract + waveform, then asr/diarize stay queued
    run_until_idle(["io"])
    run_until_idle(["gpu"], max_jobs=1)  # asr only
    db.refresh(meeting)
    assert meeting.status == "processing"  # diarize still pending

    run_until_idle(["gpu"])
    db.refresh(meeting)
    assert meeting.status == "ready"
