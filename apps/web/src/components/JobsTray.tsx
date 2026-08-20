// Pipeline progress strip — visible while a meeting is being processed.
import { useQueryClient } from "@tanstack/react-query";
import { api, type JobInfo, type MeetingDetail } from "../api/client";
import { useJobs } from "../api/hooks";

const STEPS: { type: string; label: string }[] = [
  { type: "extract_audio", label: "EXTRACT" },
  { type: "waveform", label: "WAVEFORM" },
  { type: "asr", label: "TRANSCRIBE" },
];

function stepClass(job: JobInfo | undefined): string {
  if (!job) return "tray__step";
  if (job.status === "succeeded") return "tray__step tray__step--done";
  if (job.status === "failed") return "tray__step tray__step--failed";
  if (job.status === "running") return "tray__step tray__step--running";
  return "tray__step";
}

function stepMark(job: JobInfo | undefined): string {
  if (!job) return "·";
  if (job.status === "running") {
    // live percent while transcribing — a 30-min CPU job must never look frozen
    const pct = (job.progress as { pct?: number } | null)?.pct;
    return typeof pct === "number" ? `${pct}%` : "";
  }
  return { succeeded: "✓", failed: "✕", queued: "…", cancelled: "–" }[job.status];
}

export default function JobsTray({ meeting }: { meeting: MeetingDetail }) {
  const active = meeting.status === "pending" || meeting.status === "processing";
  const { data: jobs } = useJobs(meeting.id, active);
  const queryClient = useQueryClient();
  if (!active && meeting.status !== "failed") return null;

  const byType = new Map(jobs?.map((j) => [j.type, j]));
  // speaker separation only appears once its job exists (it may be disabled)
  const steps = byType.has("diarize") ? [...STEPS, { type: "diarize", label: "SPEAKERS" }] : STEPS;
  const running = jobs?.find((j) => j.status === "running") ?? jobs?.find((j) => j.status === "queued");

  const stop = async () => {
    if (!running) return;
    await api.cancelJob(running.id);
    queryClient.invalidateQueries({ queryKey: ["jobs", meeting.id] });
    queryClient.invalidateQueries({ queryKey: ["meeting", meeting.id] });
    queryClient.invalidateQueries({ queryKey: ["meetings"] });
  };

  return (
    <div className="tray" role="status">
      {steps.map(({ type, label }) => {
        const job = byType.get(type);
        return (
          <span key={type} className={stepClass(job)}>
            {job?.status === "running" && <span className="tray__dot" />}
            {label} {stepMark(job)}
          </span>
        );
      })}
      {active && running && (
        <button className="tray__stop" onClick={stop} title="Stop processing this recording">
          ✕ Stop
        </button>
      )}
      {meeting.status === "failed" && (
        <span className="tray__error">
          Processing failed{meeting.error ? ` — ${meeting.error.split("\n")[0]}` : ""}. Re-upload
          the file to retry.
        </span>
      )}
    </div>
  );
}
