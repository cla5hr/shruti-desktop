// Record a meeting right here in the browser — mic, plus optionally the meeting's
// own audio (via tab/window share). Lands in the same pipeline as a file upload.
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { uploadRecording } from "../api/client";
import { msToClock } from "../lib/time";

type Phase = "idle" | "recording" | "uploading";

export default function RecordPanel() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [withSystem, setWithSystem] = useState(true);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamsRef = useRef<MediaStream[]>([]);
  const acRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number>(0);
  const startRef = useRef(0);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  function cleanup() {
    cancelAnimationFrame(rafRef.current);
    streamsRef.current.forEach((s) => s.getTracks().forEach((t) => t.stop()));
    streamsRef.current = [];
    acRef.current?.close().catch(() => {});
    acRef.current = null;
  }

  async function finalize() {
    setPhase("uploading");
    const blob = new Blob(chunksRef.current, { type: "audio/webm" });
    cleanup();
    try {
      const now = new Date();
      const title = `Recording ${now.toLocaleDateString("en-IN", { day: "2-digit", month: "short" })} ${now.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
      const file = new File([blob], "recording.webm", { type: "audio/webm" });
      const meeting = await uploadRecording(file, () => {}, title);
      await queryClient.invalidateQueries({ queryKey: ["meetings"] });
      navigate(`/m/${meeting.id}`);
      setPhase("idle");
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
      setPhase("idle");
    }
  }

  async function start() {
    setError(null);
    const ac = new AudioContext();
    acRef.current = ac;
    const dest = ac.createMediaStreamDestination();
    try {
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      streamsRef.current.push(mic);
      ac.createMediaStreamSource(mic).connect(dest);

      if (withSystem) {
        // user picks a tab/window/screen; we keep only its audio
        const disp = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
        streamsRef.current.push(disp);
        const sys = disp.getAudioTracks();
        if (sys.length) ac.createMediaStreamSource(new MediaStream(sys)).connect(dest);
        disp.getVideoTracks().forEach((t) => t.stop());
        if (!sys.length) {
          setError(
            "no meeting audio captured — for Teams desktop, share your ENTIRE SCREEN and " +
              "tick 'Also share system audio'; for Teams in a browser, share its tab with " +
              "'Also share tab audio'. (Sharing a window carries no audio.)",
          );
        }
      }

      const analyser = ac.createAnalyser();
      analyser.fftSize = 512;
      ac.createMediaStreamSource(dest.stream).connect(analyser);
      const buf = new Uint8Array(analyser.frequencyBinCount);

      const mime = ["audio/webm;codecs=opus", "audio/webm"].find((m) =>
        MediaRecorder.isTypeSupported(m),
      );
      const rec = new MediaRecorder(dest.stream, { mimeType: mime, audioBitsPerSecond: 96000 });
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      rec.onstop = finalize;
      rec.start(1000);
      recRef.current = rec;
      startRef.current = Date.now();
      setPhase("recording");

      const tick = () => {
        analyser.getByteTimeDomainData(buf);
        let peak = 0;
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
        setLevel(peak / 128);
        setElapsedMs(Date.now() - startRef.current);
        rafRef.current = requestAnimationFrame(tick);
      };
      tick();
    } catch (e) {
      cleanup();
      setPhase("idle");
      const msg = e instanceof Error ? e.message : "could not start recording";
      setError(msg.includes("Permission") ? "microphone/screen permission denied" : msg);
    }
  }

  function stop() {
    recRef.current?.stop();
    recRef.current = null;
  }

  if (phase === "recording") {
    return (
      <div className="record record--live">
        <div className="record__status">
          <span className="record__dot" />
          <span className="record__time">{msToClock(elapsedMs)}</span>
          <div className="record__meter">
            <div className="record__meter-fill" style={{ width: `${Math.min(100, level * 140)}%` }} />
          </div>
        </div>
        <button className="record__stop" onClick={stop}>
          Stop &amp; transcribe
        </button>
      </div>
    );
  }

  return (
    <div className="record">
      <button
        className="record__start"
        disabled={phase === "uploading"}
        onClick={start}
      >
        {phase === "uploading" ? "Uploading…" : "● Record here"}
      </button>
      <label
        className="record__opt"
        title="Teams in the browser: share its tab (tick 'Also share tab audio'). Teams desktop app: share your ENTIRE SCREEN and tick 'Also share system audio' — that captures every sound the computer plays, including Teams."
      >
        <input
          type="checkbox"
          checked={withSystem}
          onChange={(e) => setWithSystem(e.target.checked)}
          disabled={phase === "uploading"}
        />
        include meeting audio (share tab / screen)
      </label>
      {error && <div className="upload__error">{error}</div>}
    </div>
  );
}
