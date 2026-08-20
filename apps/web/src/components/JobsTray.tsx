// Pipeline progress strip — visible while a meeting is being processed.
import type { JobInfo, MeetingDetail } from "../api/client";
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
  return { succeeded: "✓", failed: "✕", running: "", queued: "…", cancelled: "–" }[job.status];
}

export default function JobsTray({ meeting }: { meeting: MeetingDetail }) {
  const active = meeting.status === "pending" || meeting.status === "processing";
  const { data: jobs } = useJobs(meeting.id, active);
  if (!active && meeting.status !== "failed") return null;

  const byType = new Map(jobs?.map((j) => [j.type, j]));
  // speaker separation only appears once its job exists (it may be disabled)
  const steps = byType.has("diarize") ? [...STEPS, { type: "diarize", label: "SPEAKERS" }] : STEPS;
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
      {meeting.status === "failed" && (
        <span className="tray__error">
          Processing failed{meeting.error ? ` — ${meeting.error.split("\n")[0]}` : ""}. Re-upload
          the file to retry.
        </span>
      )}
    </div>
  );
}
