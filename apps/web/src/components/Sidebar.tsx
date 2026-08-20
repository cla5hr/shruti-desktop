import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { api, type MeetingSummary, uploadRecording } from "../api/client";
import { useMeetings } from "../api/hooks";
import { msToClock } from "../lib/time";
import RecordPanel from "./RecordPanel";

function fmtDay(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d
    .toLocaleDateString("en-IN", { day: "2-digit", month: "short" })
    .toUpperCase();
}

const STATUS_LABEL: Record<string, string> = {
  pending: "QUEUED",
  processing: "PROCESSING",
  ready: "READY",
  failed: "FAILED",
};

function UploadZone() {
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  async function start(file: File) {
    setError(null);
    setProgress(0);
    try {
      const meeting = await uploadRecording(file, setProgress);
      await queryClient.invalidateQueries({ queryKey: ["meetings"] });
      navigate(`/m/${meeting.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
    } finally {
      setProgress(null);
    }
  }

  return (
    <div
      className={`upload${drag ? " upload--drag" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const file = e.dataTransfer.files?.[0];
        if (file) start(file);
      }}
    >
      {progress === null ? (
        <>
          <button className="upload__action" onClick={() => inputRef.current?.click()}>
            + Upload recording
          </button>
          <div className="upload__hint">or drop an audio/video file here</div>
        </>
      ) : (
        <>
          <div className="upload__action">Uploading… {Math.round(progress * 100)}%</div>
          <div className="upload__bar">
            <div className="upload__bar-fill" style={{ width: `${progress * 100}%` }} />
          </div>
        </>
      )}
      {error && <div className="upload__error">{error}</div>}
      <input
        ref={inputRef}
        type="file"
        accept="audio/*,video/*,.mp3,.wav,.m4a,.mp4,.webm,.ogg,.flac,.aac,.mkv,.wma"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) start(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}

function MeetingRow({ meeting: m }: { meeting: MeetingSummary }) {
  const [confirming, setConfirming] = useState(false);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { id } = useParams();

  async function remove(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    await api.deleteMeeting(m.id);
    await queryClient.invalidateQueries({ queryKey: ["meetings"] });
    if (id === m.id) navigate("/");
  }

  return (
    <NavLink
      to={`/m/${m.id}`}
      className={({ isActive }) => `row${isActive ? " row--active" : ""}`}
    >
      <div className="row__main">
        <div className="row__title">{m.title || "Untitled meeting"}</div>
        <div className="row__meta">
          <span>{fmtDay(m.created_at)}</span>
          {m.duration_s != null && <span>{msToClock(m.duration_s * 1000)}</span>}
          <span className={`status--${m.status}`}>{STATUS_LABEL[m.status] ?? m.status}</span>
        </div>
      </div>
      {confirming ? (
        <span className="row__confirm">
          <button className="row__del row__del--yes" onClick={remove} title="Confirm delete">
            delete
          </button>
          <button
            className="row__del"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setConfirming(false);
            }}
          >
            ✕
          </button>
        </span>
      ) : (
        <button
          className="row__del"
          title="Delete meeting"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setConfirming(true);
          }}
        >
          🗑
        </button>
      )}
    </NavLink>
  );
}

export default function Sidebar() {
  const { data: meetings, isLoading } = useMeetings();

  return (
    <aside className="sidebar">
      <Link to="/" className="wordmark" title="Home">
        <img className="wordmark__logo" src="/skyroot_logo.png" alt="Skyroot" />
        <span className="wordmark__latin">SHRUTI</span>
        <span className="wordmark__deva">श्रुति</span>
        <span className="wordmark__sub">MEETING LOG</span>
      </Link>
      <UploadZone />
      <RecordPanel />
      <nav className="log-index" aria-label="Meetings">
        <div className="log-index__head tag">Recordings</div>
        {isLoading && <div className="pane-note">LOADING…</div>}
        {meetings?.length === 0 && (
          <div className="empty">
            <span className="tag">No recordings yet</span>
            Upload a meeting to start the log.
          </div>
        )}
        {meetings?.map((m) => (
          <MeetingRow key={m.id} meeting={m} />
        ))}
      </nav>
      <NavLink to="/settings" className="sidebar__settings tag">
        ⚙ Settings
      </NavLink>
    </aside>
  );
}
