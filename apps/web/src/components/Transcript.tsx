import { useMutation, useQueryClient } from "@tanstack/react-query";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { api, type Segment } from "../api/client";
import { msToClock } from "../lib/time";

type Props = {
  meetingId: string;
  segments: Segment[];
  activeIdx: number;
  onSeek: (ms: number) => void;
  colorOf: (speakerId: string | null) => string;
  toolbarExtra?: React.ReactNode;
};

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const parts = text.split(new RegExp(`(${escapeRegExp(query)})`, "ig"));
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === query.toLowerCase() ? <mark key={i}>{part}</mark> : part,
      )}
    </>
  );
}

// memo: background polls (meetings/jobs) re-render MeetingPage every few seconds;
// re-rendering hundreds of transcript rows each time is what made inputs laggy
function Transcript({
  meetingId,
  segments,
  activeIdx,
  onSeek,
  colorOf,
  toolbarExtra,
}: Props) {
  const [query, setQuery] = useState("");
  const [hitCursor, setHitCursor] = useState(0);
  const [replaceMode, setReplaceMode] = useState(false);
  const [replaceWith, setReplaceWith] = useState("");
  const [replaceResult, setReplaceResult] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["transcript", meetingId] });

  const editSegment = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) => api.editSegment(id, text),
    onSuccess: () => {
      setEditingId(null);
      invalidate();
    },
  });

  const replaceAll = useMutation({
    mutationFn: () => api.replaceAll(meetingId, query, replaceWith),
    onSuccess: (result) => {
      setReplaceResult(`${result.replacements} replaced`);
      invalidate();
    },
  });

  const hits = useMemo(() => {
    if (!query) return [];
    const q = query.toLowerCase();
    return segments.filter((s) => s.text.toLowerCase().includes(q)).map((s) => s.idx);
  }, [segments, query]);

  useEffect(() => {
    setHitCursor(0);
    setReplaceResult(null);
  }, [query]);

  // follow the pen: keep the active utterance in view
  useEffect(() => {
    if (activeIdx < 0 || query || editingId) return;
    listRef.current
      ?.querySelector(`[data-idx="${activeIdx}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [activeIdx, query, editingId]);

  const jumpToHit = (cursor: number) => {
    setHitCursor(cursor);
    listRef.current
      ?.querySelector(`[data-idx="${hits[cursor]}"]`)
      ?.scrollIntoView({ block: "center" });
  };

  const startEdit = (s: Segment) => {
    setEditingId(s.id);
    setDraft(s.text);
  };

  return (
    <section className="logpane" aria-label="Transcript">
      <div className="logpane__bar">
        <span className="tag">Transcript</span>
        <input
          className="logpane__search"
          placeholder={replaceMode ? "find…" : "find in transcript…"}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && hits.length) jumpToHit((hitCursor + 1) % hits.length);
          }}
        />
        {query && (
          <>
            <span className="logpane__matches">
              {hits.length ? `${hitCursor + 1} of ${hits.length}` : "no matches"}
            </span>
            <button
              className="logpane__nav"
              disabled={!hits.length}
              onClick={() => jumpToHit((hitCursor - 1 + hits.length) % hits.length)}
            >
              ↑
            </button>
            <button
              className="logpane__nav"
              disabled={!hits.length}
              onClick={() => jumpToHit((hitCursor + 1) % hits.length)}
            >
              ↓
            </button>
          </>
        )}
        {replaceMode && (
          <>
            <input
              className="logpane__search logpane__search--replace"
              placeholder="replace with…"
              value={replaceWith}
              onChange={(e) => setReplaceWith(e.target.value)}
            />
            <button
              className="barbtn"
              disabled={!query || replaceAll.isPending}
              onClick={() => replaceAll.mutate()}
            >
              Replace all
            </button>
            {replaceResult && <span className="logpane__matches">{replaceResult}</span>}
          </>
        )}
        <span className="logpane__spacer" />
        <button
          className={`barbtn${replaceMode ? " barbtn--on" : ""}`}
          onClick={() => setReplaceMode((v) => !v)}
        >
          Replace
        </button>
        {toolbarExtra}
      </div>
      <div className="log" ref={listRef}>
        {segments.map((s) => (
          <div
            key={s.id}
            data-idx={s.idx}
            className={[
              "utt",
              s.idx === activeIdx ? "utt--active" : "",
              query && hits[hitCursor] === s.idx ? "utt--hit-current" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <button className="utt__ts" onClick={() => onSeek(s.start_ms)} title="Play from here">
              {msToClock(s.start_ms)}
            </button>
            <div className="utt__body">
              {s.speaker && (
                <span className="utt__speaker">
                  <span className="nib" style={{ background: colorOf(s.speaker_id) }} />
                  {s.speaker}
                </span>
              )}
              {editingId === s.id ? (
                <textarea
                  className="utt__edit"
                  value={draft}
                  autoFocus
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      editSegment.mutate({ id: s.id, text: draft });
                    }
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  onBlur={() => editSegment.mutate({ id: s.id, text: draft })}
                />
              ) : (
                <span
                  className="utt__text"
                  onDoubleClick={() => startEdit(s)}
                  title="Double-click to edit"
                >
                  <Highlight text={s.text} query={query} />
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export default memo(Transcript);
