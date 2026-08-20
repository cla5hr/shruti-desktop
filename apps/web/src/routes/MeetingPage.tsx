import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { useMeeting, usePeaks, useSpeakers, useTranscript } from "../api/hooks";
import ChatPane from "../components/ChatPane";
import JobsTray from "../components/JobsTray";
import SpeakerPanel from "../components/SpeakerPanel";
import StripChart from "../components/StripChart";
import SummaryPane from "../components/SummaryPane";
import Transcript from "../components/Transcript";
import { findActiveIndex } from "../lib/segments";
import { msToClock } from "../lib/time";

const RATES = [1, 1.25, 1.5, 2];
// multi-pen recorder inks — speaker identity colors
const PENS = ["#147a5c", "#3e5c9a", "#8a5a2b", "#7c4e8f", "#2e7c8a", "#6b6b2a"];

function fmtStamp(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const day = d
    .toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })
    .toUpperCase();
  const time = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day} · ${time}`;
}

function EditableTitle({ meetingId, title }: { meetingId: string; title: string }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const queryClient = useQueryClient();

  const save = async () => {
    const t = draft.trim();
    setEditing(false);
    if (!t || t === title) return;
    await api.renameMeeting(meetingId, t);
    queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
    queryClient.invalidateQueries({ queryKey: ["meetings"] });
  };

  if (editing) {
    return (
      <input
        className="masthead__title masthead__title-input"
        value={draft}
        autoFocus
        maxLength={300}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") setEditing(false);
        }}
      />
    );
  }
  return (
    <h1
      className="masthead__title masthead__title--editable"
      title="Click to rename"
      aria-label={`${title || "Untitled meeting"} — click to rename`}
      onClick={() => {
        setDraft(title);
        setEditing(true);
      }}
    >
      {title || "Untitled meeting"}
    </h1>
  );
}

export default function MeetingPage() {
  const { id } = useParams<{ id: string }>();
  const { data: meeting } = useMeeting(id);
  const ready = meeting?.status === "ready";
  const { data: transcript } = useTranscript(id, !!ready);
  const { data: peaks } = usePeaks(id, !!meeting?.recording?.has_peaks);
  const { data: speakers } = useSpeakers(id, !!ready);
  const queryClient = useQueryClient();

  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [rateIdx, setRateIdx] = useState(0);
  const [currentMs, setCurrentMs] = useState(0);
  const [showSpeakers, setShowSpeakers] = useState(false);
  const [confirmRerun, setConfirmRerun] = useState(false);
  const [tab, setTab] = useState<"minutes" | "transcript" | "ask">("minutes");

  // low-frequency clock for transcript highlight + readout (canvas runs its own rAF)
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => {
      setCurrentMs((audioRef.current?.currentTime ?? 0) * 1000);
    }, 200);
    return () => clearInterval(t);
  }, [playing]);

  const seek = useCallback((ms: number) => {
    const el = audioRef.current;
    if (!el) return;
    el.currentTime = ms / 1000;
    setCurrentMs(ms);
  }, []);

  const togglePlay = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    if (el.paused) el.play();
    else el.pause();
  }, []);

  const cycleRate = useCallback(() => {
    const next = (rateIdx + 1) % RATES.length;
    setRateIdx(next);
    if (audioRef.current) audioRef.current.playbackRate = RATES[next];
  }, [rateIdx]);

  const retranscribe = useMutation({
    mutationFn: () => api.retranscribe(id!),
    onSuccess: () => {
      setConfirmRerun(false);
      queryClient.invalidateQueries({ queryKey: ["meeting", id] });
    },
  });

  const colorOf = useCallback(
    (speakerId: string | null) => {
      if (!speakerId || !speakers) return "var(--ink-faint)";
      const i = speakers.findIndex((s) => s.id === speakerId);
      return i >= 0 ? PENS[i % PENS.length] : "var(--ink-faint)";
    },
    [speakers],
  );

  const playSample = useCallback(
    (speakerId: string) => {
      const seg = transcript?.segments.find((s) => s.speaker_id === speakerId);
      if (seg) {
        seek(seg.start_ms);
        audioRef.current?.play();
      }
    },
    [transcript, seek],
  );

  // stable element so the memoized Transcript isn't re-rendered by unrelated polls
  const speakersButton = useMemo(
    () => (
      <button
        className={`barbtn${showSpeakers ? " barbtn--on" : ""}`}
        onClick={() => setShowSpeakers((v) => !v)}
      >
        Speakers{speakers ? ` (${speakers.length})` : ""}
      </button>
    ),
    [showSpeakers, speakers],
  );

  const starts = useMemo(() => transcript?.segments.map((s) => s.start_ms) ?? [], [transcript]);
  const activeIdx = findActiveIndex(starts, currentMs);
  const durationMs = (meeting?.recording?.duration_s ?? meeting?.duration_s ?? 0) * 1000;

  if (!meeting) return <div className="pane-note">LOADING LOG…</div>;

  return (
    <main className="workspace">
      <header className="masthead">
        <div>
          <div className="tag masthead__eyebrow">Meeting recording</div>
          <EditableTitle meetingId={meeting.id} title={meeting.title} />
        </div>
        {ready && (
          <div className="masthead__actions">
            <a className="barbtn" href={api.exportMdUrl(meeting.id)} download>
              Export MD
            </a>
            {confirmRerun ? (
              <button
                className="barbtn barbtn--on"
                onClick={() => retranscribe.mutate()}
                onBlur={() => setConfirmRerun(false)}
              >
                Confirm re-run?
              </button>
            ) : (
              <button className="barbtn" onClick={() => setConfirmRerun(true)}>
                Retranscribe
              </button>
            )}
          </div>
        )}
        <div className="masthead__meta">
          {fmtStamp(meeting.created_at)}
          {durationMs > 0 && <> · {msToClock(durationMs)}</>}
          <br />
          <span className={`status--${meeting.status}`}>{meeting.status.toUpperCase()}</span>
          {transcript && <> · {transcript.model}</>}
        </div>
      </header>

      <JobsTray meeting={meeting} />

      {meeting.recording?.has_playback && (
        <audio
          ref={audioRef}
          src={api.audioUrl(meeting.id)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          preload="metadata"
        />
      )}

      {peaks && durationMs > 0 && (
        <StripChart
          peaks={peaks.peaks}
          durationMs={durationMs}
          segmentStartsMs={starts}
          audioRef={audioRef}
          playing={playing}
          onTogglePlay={togglePlay}
          onSeek={seek}
          rate={RATES[rateIdx]}
          onCycleRate={cycleRate}
          currentMs={currentMs}
        />
      )}

      {transcript ? (
        <>
          <div className="tabs" role="tablist">
            <button
              className={`tab${tab === "minutes" ? " tab--on" : ""}`}
              role="tab"
              aria-selected={tab === "minutes"}
              onClick={() => setTab("minutes")}
            >
              Minutes
            </button>
            <button
              className={`tab${tab === "transcript" ? " tab--on" : ""}`}
              role="tab"
              aria-selected={tab === "transcript"}
              onClick={() => setTab("transcript")}
            >
              Transcript
            </button>
            <button
              className={`tab${tab === "ask" ? " tab--on" : ""}`}
              role="tab"
              aria-selected={tab === "ask"}
              onClick={() => setTab("ask")}
            >
              Ask
            </button>
          </div>
          {tab === "minutes" ? (
            <SummaryPane meetingId={meeting.id} />
          ) : tab === "ask" ? (
            <ChatPane meetingId={meeting.id} onSeek={seek} />
          ) : (
            <>
              {showSpeakers && speakers && (
                <SpeakerPanel
                  meetingId={meeting.id}
                  speakers={speakers}
                  colorOf={colorOf}
                  onPlaySample={playSample}
                />
              )}
              <Transcript
                meetingId={meeting.id}
                segments={transcript.segments}
                activeIdx={activeIdx}
                onSeek={seek}
                colorOf={colorOf}
                toolbarExtra={speakersButton}
              />
            </>
          )}
        </>
      ) : (
        meeting.status !== "failed" && (
          <div className="pane-note">
            {ready ? "LOADING TRANSCRIPT…" : "TRANSCRIPT APPEARS HERE ONCE PROCESSING COMPLETES"}
          </div>
        )
      )}
    </main>
  );
}
