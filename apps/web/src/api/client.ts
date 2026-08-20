export type MeetingSummary = {
  id: string;
  title: string;
  source: string;
  status: "pending" | "processing" | "ready" | "failed";
  duration_s: number | null;
  created_at: string | null;
};

export type MeetingDetail = MeetingSummary & {
  recording: {
    id: string;
    original_filename: string;
    duration_s: number | null;
    size_bytes: number | null;
    has_playback: boolean;
    has_peaks: boolean;
  } | null;
  active_transcript_id: string | null;
  error?: string | null;
};

export type Segment = {
  id: string;
  idx: number;
  start_ms: number;
  end_ms: number;
  speaker_id: string | null;
  speaker: string | null;
  text: string;
};

export type SpeakerInfo = {
  id: string;
  display_name: string;
  source_label: string;
  segment_count: number;
};

export type TranscriptData = {
  id: string;
  kind: string;
  engine: string;
  model: string;
  language: string | null;
  segments: Segment[];
};

export type JobInfo = {
  id: string;
  type: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  attempts: number;
  progress: Record<string, unknown> | null;
  error: string | null;
};

export type PeaksData = { version: number; peaks: [number, number][] };

export type AppSettingsValues = {
  asr_model: string;
  asr_device: string;
  asr_compute_type: string;
  diarize_num_speakers: number;
  llm_mode: string;
  llm_base_url: string;
  llm_model: string;
  llm_api_key: string;
  llm_max_ctx: number;
};

export type AppSettings = {
  values: AppSettingsValues;
  meta: {
    editable: boolean;
    diarization_available: boolean;
    cuda_available: boolean;
    asr_models: {
      id: string;
      label: string;
      detail: string;
      sizeMB: number;
      installed: boolean | null;
    }[];
    ollama: { running: boolean; models: string[] };
  };
};

export type ChatThreadInfo = { id: string; title: string; created_at: string | null };
export type ChatMsg = { id: string; role: "user" | "assistant"; content: string };

export type SummaryData = {
  id: string;
  template_key: string;
  content_md: string;
  model: string;
  edited: boolean;
  created_at: string | null;
};

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${url}`);
  return resp.json();
}

async function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  const resp = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) {
    let message = `${resp.status} ${url}`;
    try {
      message = (await resp.json()).detail ?? message;
    } catch {
      /* keep default */
    }
    throw new Error(message);
  }
  return resp.json();
}

export const api = {
  meetings: () => getJson<MeetingSummary[]>("/api/meetings"),
  settings: () => getJson<AppSettings>("/api/settings"),
  saveSettings: (values: Partial<AppSettingsValues>) =>
    send<AppSettings>("/api/settings", "PUT", { values }),
  prefetchAsr: (model: string) =>
    send<JobInfo>("/api/settings/prefetch-asr", "POST", { model }),
  testLlm: (llm_base_url: string, llm_model: string, llm_api_key: string) =>
    send<{ ok: boolean; detail: string }>("/api/settings/test-llm", "POST", {
      llm_base_url,
      llm_model,
      llm_api_key,
    }),
  cancelJob: (id: string) => send<JobInfo>(`/api/jobs/${id}/cancel`, "POST"),
  job: (id: string) => getJson<JobInfo>(`/api/jobs/${id}`),
  meeting: (id: string) => getJson<MeetingDetail>(`/api/meetings/${id}`),
  transcript: (id: string) => getJson<TranscriptData>(`/api/meetings/${id}/transcript`),
  peaks: (id: string) => getJson<PeaksData>(`/api/meetings/${id}/peaks`),
  jobs: (meetingId: string) => getJson<JobInfo[]>(`/api/jobs?meeting_id=${meetingId}`),
  speakers: (meetingId: string) => getJson<SpeakerInfo[]>(`/api/meetings/${meetingId}/speakers`),
  summary: async (meetingId: string): Promise<SummaryData | null> => {
    const resp = await fetch(`/api/meetings/${meetingId}/summary`);
    if (resp.status === 404) return null;
    if (!resp.ok) throw new Error(`${resp.status} summary`);
    return resp.json();
  },
  audioUrl: (id: string) => `/api/meetings/${id}/audio`,
  exportMdUrl: (id: string) => `/api/meetings/${id}/export.md`,

  renameMeeting: (id: string, title: string) =>
    send<MeetingDetail>(`/api/meetings/${id}`, "PATCH", { title }),
  renameSpeaker: (id: string, display_name: string) =>
    send<SpeakerInfo>(`/api/speakers/${id}`, "PATCH", { display_name }),
  mergeSpeaker: (id: string, intoId: string) =>
    send<SpeakerInfo>(`/api/speakers/${id}/merge`, "POST", { into_speaker_id: intoId }),
  editSegment: (id: string, text: string) =>
    send<{ id: string; text: string }>(`/api/segments/${id}`, "PATCH", { text }),
  replaceAll: (meetingId: string, find: string, replace: string, matchCase = false) =>
    send<{ replacements: number; segments_changed: number }>(
      `/api/meetings/${meetingId}/replace`,
      "POST",
      { find, replace, match_case: matchCase },
    ),
  retranscribe: (meetingId: string) =>
    send<JobInfo>(`/api/meetings/${meetingId}/retranscribe`, "POST"),
  rediarize: (meetingId: string) =>
    send<JobInfo>(`/api/meetings/${meetingId}/rediarize`, "POST"),
  deleteMeeting: async (id: string): Promise<void> => {
    const resp = await fetch(`/api/meetings/${id}`, { method: "DELETE" });
    if (!resp.ok && resp.status !== 204) throw new Error(`delete failed (${resp.status})`);
  },
  editSummary: (meetingId: string, content_md: string) =>
    send<SummaryData>(`/api/meetings/${meetingId}/summary`, "PUT", { content_md }),
  requestSummary: (meetingId: string, template_key = "standard") =>
    send<JobInfo>(`/api/meetings/${meetingId}/summarize`, "POST", { template_key }),
  threads: (meetingId: string) =>
    getJson<ChatThreadInfo[]>(`/api/meetings/${meetingId}/threads`),
  createThread: (meetingId: string) =>
    send<ChatThreadInfo>(`/api/meetings/${meetingId}/threads`, "POST", {}),
  messages: (threadId: string) => getJson<ChatMsg[]>(`/api/threads/${threadId}/messages`),
};

/** POST a question and consume the SSE reply stream. */
export async function askStream(
  threadId: string,
  content: string,
  handlers: {
    onDelta: (delta: string) => void;
    onDone: () => void;
    onError: (message: string) => void;
  },
): Promise<void> {
  const resp = await fetch(`/api/threads/${threadId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!resp.ok || !resp.body) {
    handlers.onError(`ask failed (${resp.status})`);
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line.startsWith("data:")) continue;
      const event = JSON.parse(line.slice(5));
      if (event.delta) handlers.onDelta(event.delta);
      if (event.error) {
        handlers.onError(event.error);
        return;
      }
      if (event.done) {
        handlers.onDone();
        return;
      }
    }
  }
  handlers.onDone();
}

/** Upload with progress via XHR (fetch has no upload progress events). */
export function uploadRecording(
  file: File,
  onProgress: (fraction: number) => void,
  title = "",
): Promise<MeetingDetail> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status === 201) resolve(JSON.parse(xhr.responseText));
      else {
        try {
          reject(new Error(JSON.parse(xhr.responseText).detail ?? `upload failed (${xhr.status})`));
        } catch {
          reject(new Error(`upload failed (${xhr.status})`));
        }
      }
    };
    xhr.onerror = () => reject(new Error("upload failed (network)"));
    const form = new FormData();
    form.append("file", file);
    if (title) form.append("title", title);
    xhr.send(form);
  });
}
