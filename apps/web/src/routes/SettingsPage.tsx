import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type AppSettingsValues } from "../api/client";
import { useAppSettings } from "../api/hooks";

/** In-app settings (desktop build): transcription model, speakers, minutes provider. */
export default function SettingsPage() {
  const { data, isLoading } = useAppSettings();
  const qc = useQueryClient();
  const [form, setForm] = useState<AppSettingsValues | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "downloading" | "saved" | "error">(
    "idle",
  );
  const [error, setError] = useState("");
  const [download, setDownload] = useState<{ pct: number; mb: number; total: number } | null>(
    null,
  );
  const [test, setTest] = useState<{ state: "idle" | "testing" | "ok" | "fail"; msg: string }>({
    state: "idle",
    msg: "",
  });

  useEffect(() => {
    if (data && form === null) setForm({ ...data.values });
  }, [data, form]);

  if (isLoading || !data || !form) {
    return (
      <main className="workspace">
        <div className="pane-note">LOADING SETTINGS…</div>
      </main>
    );
  }

  const { meta } = data;
  const set = <K extends keyof AppSettingsValues>(key: K, value: AppSettingsValues[K]) => {
    setForm({ ...form, [key]: value });
    setStatus("idle");
  };

  // The provider select folds llm_mode + base URL into one honest choice.
  const provider =
    form.llm_mode !== "live" ? "off" : form.llm_base_url.includes(":11434") ? "ollama" : "endpoint";

  const setProvider = (p: string) => {
    if (p === "off") setForm({ ...form, llm_mode: "stub" });
    else if (p === "ollama")
      setForm({ ...form, llm_mode: "live", llm_base_url: "http://localhost:11434/v1" });
    else setForm({ ...form, llm_mode: "live", llm_base_url: "" });
    setStatus("idle");
    setTest({ state: "idle", msg: "" });
  };

  const testConnection = async () => {
    setTest({ state: "testing", msg: "" });
    try {
      const base =
        provider === "ollama" ? "http://localhost:11434/v1" : form.llm_base_url;
      const r = await api.testLlm(base, form.llm_model, form.llm_api_key);
      setTest({ state: r.ok ? "ok" : "fail", msg: r.detail });
    } catch (e) {
      setTest({ state: "fail", msg: e instanceof Error ? e.message : "test failed" });
    }
  };

  const save = async () => {
    setStatus("saving");
    setDownload(null);
    try {
      await api.saveSettings(form);
      // if the chosen transcription model isn't on disk yet, fetch it NOW with a
      // visible progress bar instead of a silent stall on the first transcription
      const chosen = meta.asr_models.find((m) => m.id === form.asr_model);
      if (chosen && chosen.installed === false) {
        setStatus("downloading");
        const job = await api.prefetchAsr(chosen.id);
        for (;;) {
          await new Promise((r) => setTimeout(r, 1000));
          const j = await api.job(job.id);
          const p = (j.progress ?? {}) as { pct?: number; downloaded_mb?: number };
          setDownload({
            pct: j.status === "succeeded" ? 100 : (p.pct ?? 0),
            mb: p.downloaded_mb ?? 0,
            total: chosen.sizeMB,
          });
          if (j.status === "succeeded") break;
          if (j.status === "failed" || j.status === "cancelled") {
            // job errors are full tracebacks; the actionable message is the last line
            const lastLine = (j.error ?? "").trim().split("\n").at(-1) ?? "";
            throw new Error(
              lastLine.replace(/^RuntimeError:\s*/, "") ||
                "model download failed — check your internet and press Save again",
            );
          }
        }
      }
      await qc.invalidateQueries({ queryKey: ["settings"] });
      setStatus("saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
      setStatus("error");
    }
  };

  return (
    <main className="workspace">
      <div className="settings">
        <h1 className="settings__title">SETTINGS</h1>
        {!meta.editable && (
          <p className="settings__note">
            This deployment's settings are managed by its administrator (read-only here).
          </p>
        )}

        <section className="settings__section">
          <div className="tag settings__head">Transcription</div>
          <label className="settings__row">
            <span className="settings__label">Model</span>
            <select
              className="settings__input"
              value={form.asr_model}
              onChange={(e) => set("asr_model", e.target.value)}
            >
              {meta.asr_models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} — {m.detail} (~{m.sizeMB} MB{m.installed ? " · downloaded" : ""})
                </option>
              ))}
            </select>
          </label>
          <p className="settings__hint">
            Models download automatically the first time they are used, then work offline.
            Larger models are more accurate and slower.
          </p>
          {meta.cuda_available && (
            <label className="settings__row">
              <span className="settings__label">Device</span>
              <select
                className="settings__input"
                value={form.asr_device}
                onChange={(e) => set("asr_device", e.target.value)}
              >
                <option value="cpu">CPU</option>
                <option value="cuda">NVIDIA GPU (faster)</option>
              </select>
            </label>
          )}
        </section>

        <section className="settings__section">
          <div className="tag settings__head">Speaker separation</div>
          {meta.diarization_available ? (
            <>
              <label className="settings__row">
                <span className="settings__label">People in meeting</span>
                <input
                  className="settings__input"
                  type="number"
                  min={0}
                  max={30}
                  value={form.diarize_num_speakers}
                  onChange={(e) => set("diarize_num_speakers", Number(e.target.value) || 0)}
                />
              </label>
              <p className="settings__hint">0 = detect automatically.</p>
            </>
          ) : (
            <p className="settings__hint">
              Not included in this build — transcripts are single-speaker for now.
            </p>
          )}
        </section>

        <section className="settings__section">
          <div className="tag settings__head">Minutes &amp; Q-and-A</div>
          <label className="settings__row">
            <span className="settings__label">AI provider</span>
            <select className="settings__input" value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="ollama">Ollama on this computer (private, free)</option>
              <option value="endpoint">Hosted API provider (OpenAI-compatible)</option>
              <option value="off">Off (no AI minutes)</option>
            </select>
          </label>
          {provider === "ollama" && !meta.ollama.running && (
            <p className="settings__hint">
              Ollama isn't running. Install from ollama.com, then run:{" "}
              <code>ollama pull qwen2.5:3b</code>
            </p>
          )}
          {provider === "ollama" &&
            (meta.ollama.models.length > 0 ? (
              <label className="settings__row">
                <span className="settings__label">Model</span>
                <select
                  className="settings__input"
                  value={form.llm_model}
                  onChange={(e) => set("llm_model", e.target.value)}
                >
                  {!meta.ollama.models.includes(form.llm_model) && (
                    <option value={form.llm_model}>{form.llm_model} (not installed)</option>
                  )}
                  {meta.ollama.models.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <label className="settings__row">
                <span className="settings__label">Model</span>
                <input
                  className="settings__input"
                  placeholder="qwen2.5:3b"
                  value={form.llm_model}
                  onChange={(e) => set("llm_model", e.target.value)}
                />
              </label>
            ))}
          {provider === "endpoint" && (
            <>
              <p className="settings__hint">
                Works with any OpenAI-compatible service (company server, OpenAI, Groq…).
                The base URL and model name are in your provider's docs; the API key is
                the secret they give you. Self-hosted servers often need no key.
              </p>
              <label className="settings__row">
                <span className="settings__label">Base URL (with port)</span>
                <input
                  className="settings__input"
                  placeholder="e.g. http://192.168.1.50:11434/v1 — include port + /v1"
                  value={form.llm_base_url}
                  onChange={(e) => set("llm_base_url", e.target.value)}
                />
              </label>
              <p className="settings__hint">
                Include the port if the server uses one (e.g. <code>:11434</code> for
                Ollama, <code>:8080</code> for llama.cpp). Paths usually end in{" "}
                <code>/v1</code>. Cloud providers need no port:{" "}
                <code>https://api.groq.com/openai/v1</code>
              </p>
              <label className="settings__row">
                <span className="settings__label">API key</span>
                <input
                  className="settings__input"
                  type="password"
                  placeholder="sk-… (leave empty if your server needs none)"
                  value={form.llm_api_key}
                  onChange={(e) => set("llm_api_key", e.target.value)}
                />
              </label>
              <label className="settings__row">
                <span className="settings__label">Model</span>
                <input
                  className="settings__input"
                  placeholder="e.g. gpt-4o-mini"
                  value={form.llm_model}
                  onChange={(e) => set("llm_model", e.target.value)}
                />
              </label>
            </>
          )}
          {provider !== "off" && (
            <div className="settings__row">
              <span className="settings__label" />
              <button
                className="barbtn"
                disabled={test.state === "testing"}
                onClick={testConnection}
              >
                {test.state === "testing" ? "Testing…" : "Test connection"}
              </button>
              {test.state === "ok" && <span className="settings__ok">✓ {test.msg}</span>}
              {test.state === "fail" && <span className="upload__error">✕ {test.msg}</span>}
            </div>
          )}
        </section>

        {meta.editable && (
          <div className="settings__actions">
            <button
              className="settings__save"
              onClick={save}
              disabled={status === "saving" || status === "downloading"}
            >
              {status === "saving"
                ? "SAVING…"
                : status === "downloading"
                  ? "DOWNLOADING…"
                  : "SAVE SETTINGS"}
            </button>
            {status === "downloading" && download && (
              <div className="settings__dl">
                <div className="settings__dlbar">
                  <div className="settings__dlfill" style={{ width: `${download.pct}%` }} />
                </div>
                <span className="settings__dltext">
                  {download.mb} / ~{download.total} MB
                </span>
              </div>
            )}
            {status === "saved" && (
              <span className="settings__ok">Saved — model ready, applies immediately.</span>
            )}
            {status === "error" && <span className="upload__error">{error}</span>}
          </div>
        )}
      </div>
    </main>
  );
}
