// Ask the meeting anything. Answers stream in live; timestamp citations become
// click-to-seek buttons that drive the strip-chart pen.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, askStream, type ChatMsg } from "../api/client";
import { splitCitations } from "../lib/citations";

const SUGGESTIONS = [
  "What is the immediate thing we should do now?",
  "List all action items with owners.",
  "What decisions were made?",
];

function Answer({ text, onSeek }: { text: string; onSeek: (ms: number) => void }) {
  return (
    <>
      {splitCitations(text).map((chunk, i) =>
        chunk.type === "text" ? (
          <span key={i}>{chunk.value}</span>
        ) : (
          <button key={i} className="chat__cite" onClick={() => onSeek(chunk.ms)}>
            {chunk.label}
          </button>
        ),
      )}
    </>
  );
}

export default function ChatPane({
  meetingId,
  onSeek,
}: {
  meetingId: string;
  onSeek: (ms: number) => void;
}) {
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const [draft, setDraft] = useState<string | null>(null); // streaming assistant text
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const { data: threads } = useQuery({
    queryKey: ["threads", meetingId],
    queryFn: () => api.threads(meetingId),
  });
  const threadId = threads?.[0]?.id ?? null;

  const { data: messages } = useQuery({
    queryKey: ["messages", threadId],
    queryFn: () => api.messages(threadId!),
    enabled: !!threadId,
  });

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [messages, draft, pendingQuestion]);

  const asking = pendingQuestion !== null;

  const ask = useMutation({
    mutationFn: async (question: string) => {
      setError(null);
      setPendingQuestion(question);
      setDraft("");
      let tid = threadId;
      if (!tid) {
        tid = (await api.createThread(meetingId)).id;
        await queryClient.invalidateQueries({ queryKey: ["threads", meetingId] });
      }
      let acc = "";
      await askStream(tid, question, {
        onDelta: (d) => {
          acc += d;
          setDraft(acc);
        },
        onDone: () => undefined,
        onError: (msg) => setError(msg),
      });
      await queryClient.invalidateQueries({ queryKey: ["messages", tid] });
      setPendingQuestion(null);
      setDraft(null);
    },
  });

  const send = () => {
    const q = input.trim();
    if (!q || asking) return;
    setInput("");
    ask.mutate(q);
  };

  const shown: ChatMsg[] = messages ?? [];
  const empty = shown.length === 0 && !asking;

  return (
    <section className="logpane" aria-label="Ask the meeting">
      <div className="chat__log log" ref={logRef}>
        {empty && (
          <div className="chat__welcome">
            <span className="tag">Ask this meeting anything</span>
            <div className="chat__suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chat__suggestion" onClick={() => ask.mutate(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {shown.map((m) => (
          <div key={m.id} className={`chat__msg chat__msg--${m.role}`}>
            <span className="chat__who">{m.role === "user" ? "Q" : "A"}</span>
            <div className="chat__body">
              {m.role === "assistant" ? <Answer text={m.content} onSeek={onSeek} /> : m.content}
            </div>
          </div>
        ))}
        {asking && (
          <>
            <div className="chat__msg chat__msg--user">
              <span className="chat__who">Q</span>
              <div className="chat__body">{pendingQuestion}</div>
            </div>
            <div className="chat__msg chat__msg--assistant">
              <span className="chat__who">A</span>
              <div className="chat__body">
                {draft ? (
                  <Answer text={draft} onSeek={onSeek} />
                ) : (
                  <span className="chat__thinking">
                    <span className="tray__dot" /> consulting the log…
                  </span>
                )}
              </div>
            </div>
          </>
        )}
        {error && <div className="pane-note pane-note--error">{error}</div>}
      </div>
      <div className="chat__inputbar">
        <input
          className="chat__input"
          placeholder="ask about this meeting…"
          value={input}
          disabled={asking}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button className="barbtn" onClick={send} disabled={asking || !input.trim()}>
          Ask
        </button>
      </div>
    </section>
  );
}
