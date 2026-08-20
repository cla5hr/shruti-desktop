// Minutes of Meeting: rendered markdown from the local LLM, editable, regenerable.
// Minutes are generated AFTER a meeting is ready, so this pane owns its own
// "generating…" state by watching the summarize job.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../api/client";
import { useJobs } from "../api/hooks";

const TEMPLATES = [
  { key: "standard", label: "Standard minutes" },
  { key: "brief", label: "Brief" },
];

export default function SummaryPane({ meetingId }: { meetingId: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [template, setTemplate] = useState("standard");
  const [copied, setCopied] = useState(false);

  const { data: summary, isLoading } = useQuery({
    queryKey: ["summary", meetingId],
    queryFn: () => api.summary(meetingId),
  });

  // poll the job list only while something can actually change — an always-on 2s
  // poll re-rendered the whole page forever and made typing laggy
  const [watchJobs, setWatchJobs] = useState(true);
  const { data: jobs } = useJobs(meetingId, watchJobs);
  const summarizeJobs = useMemo(
    () => (jobs ?? []).filter((j) => j.type === "summarize"),
    [jobs],
  );
  const generating = summarizeJobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    setWatchJobs(generating || !summary);
  }, [generating, summary]);
  const lastFailed = !generating && summarizeJobs[0]?.status === "failed" ? summarizeJobs[0] : null;

  // when a summarize job finishes, pull the fresh minutes
  const doneCount = summarizeJobs.filter((j) => j.status === "succeeded").length;
  useEffect(() => {
    if (doneCount > 0) queryClient.invalidateQueries({ queryKey: ["summary", meetingId] });
  }, [doneCount, queryClient, meetingId]);

  const regenerate = useMutation({
    mutationFn: () => api.requestSummary(meetingId, template),
    onSuccess: () => {
      setWatchJobs(true);
      queryClient.invalidateQueries({ queryKey: ["jobs", meetingId] });
    },
  });

  const save = useMutation({
    mutationFn: () => api.editSummary(meetingId, draft),
    onSuccess: () => {
      setEditing(false);
      queryClient.invalidateQueries({ queryKey: ["summary", meetingId] });
    },
  });

  const copy = async () => {
    if (!summary) return;
    await navigator.clipboard.writeText(summary.content_md);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <section className="logpane" aria-label="Minutes">
      <div className="logpane__bar">
        <span className="tag">Minutes</span>
        {summary && (
          <span className="logpane__matches">
            {summary.model}
            {summary.edited ? " · edited" : ""}
          </span>
        )}
        <span className="logpane__spacer" />
        <select
          className="speakers__select"
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          aria-label="Minutes template"
        >
          {TEMPLATES.map((t) => (
            <option key={t.key} value={t.key}>
              {t.label}
            </option>
          ))}
        </select>
        <button
          className="barbtn"
          disabled={generating || regenerate.isPending}
          onClick={() => regenerate.mutate()}
        >
          {summary ? "Regenerate" : "Generate"}
        </button>
        {summary && !editing && (
          <>
            <button
              className="barbtn"
              onClick={() => {
                setDraft(summary.content_md);
                setEditing(true);
              }}
            >
              Edit
            </button>
            <button className="barbtn" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
          </>
        )}
        {editing && (
          <>
            <button className="barbtn barbtn--on" onClick={() => save.mutate()}>
              Save
            </button>
            <button className="barbtn" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </>
        )}
      </div>

      <div className="log">
        {editing ? (
          <textarea
            className="summary-editor"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        ) : summary ? (
          <article className="summary-md">
            <ReactMarkdown>{summary.content_md}</ReactMarkdown>
          </article>
        ) : generating ? (
          <div className="pane-note">
            <span className="tray__dot" /> MINUTES GENERATING — THE PEN IS WRITING…
          </div>
        ) : lastFailed ? (
          <div className="pane-note pane-note--error">
            MINUTES FAILED: {(lastFailed.error ?? "").split("\n")[0].slice(0, 200)}
            <br />
            Check that ollama is running, then use Generate above.
          </div>
        ) : isLoading ? (
          <div className="pane-note">LOADING…</div>
        ) : (
          <div className="pane-note">NO MINUTES YET — USE GENERATE ABOVE</div>
        )}
      </div>
    </section>
  );
}
