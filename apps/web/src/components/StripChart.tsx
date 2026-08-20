// The signature element: audio as a strip-chart recorder trace.
// Peaks (precomputed server-side) draw as a pen envelope on gridded chart paper;
// the playhead is the event pen sweeping the chart. Canvas redraws on rAF by
// reading the <audio> element directly — React state stays out of the 60fps path.
import { useCallback, useEffect, useRef, useState } from "react";
import { msToClock } from "../lib/time";

type Props = {
  peaks: [number, number][];
  durationMs: number;
  segmentStartsMs: number[];
  audioRef: React.RefObject<HTMLAudioElement | null>;
  playing: boolean;
  onTogglePlay: () => void;
  onSeek: (ms: number) => void;
  rate: number;
  onCycleRate: () => void;
  currentMs: number; // low-frequency mirror for the readout text
};

const RULER_H = 22;

function gridSteps(durationMs: number): { minor: number; major: number } {
  const s = durationMs / 1000;
  if (s <= 180) return { minor: 5_000, major: 30_000 };
  if (s <= 720) return { minor: 10_000, major: 60_000 };
  if (s <= 2700) return { minor: 30_000, major: 300_000 };
  return { minor: 60_000, major: 600_000 };
}

export default function StripChart({
  peaks,
  durationMs,
  segmentStartsMs,
  audioRef,
  playing,
  onTogglePlay,
  onSeek,
  rate,
  onCycleRate,
  currentMs,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ x: number; ms: number } | null>(null);
  const draggingRef = useRef(false);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || durationMs <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
    }
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const css = getComputedStyle(document.documentElement);
    const col = (name: string) => css.getPropertyValue(name).trim();
    const paper = col("--paper-raised");
    const grid = col("--grid");
    const rule = col("--rule");
    const ink = col("--ink");
    const inkFaint = col("--ink-faint");
    const pen = col("--pen");
    const signal = col("--signal");

    const waveH = cssH - RULER_H;
    const midY = waveH / 2;
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.fillStyle = paper;
    ctx.fillRect(0, 0, cssW, waveH);

    // chart grid: horizontals
    ctx.strokeStyle = grid;
    ctx.lineWidth = 1;
    for (const fy of [0.25, 0.5, 0.75]) {
      ctx.beginPath();
      ctx.moveTo(0, Math.round(fy * waveH) + 0.5);
      ctx.lineTo(cssW, Math.round(fy * waveH) + 0.5);
      ctx.stroke();
    }
    // verticals are time-true: minor/major intervals derived from duration
    const { minor, major } = gridSteps(durationMs);
    const xOf = (ms: number) => (ms / durationMs) * cssW;
    ctx.font = "9px IBM Plex Mono, monospace";
    ctx.textAlign = "center";
    for (let t = 0; t <= durationMs; t += minor) {
      const isMajor = t % major === 0;
      const x = Math.round(xOf(t)) + 0.5;
      ctx.strokeStyle = isMajor ? rule : grid;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, waveH);
      ctx.stroke();
      if (isMajor) {
        ctx.fillStyle = inkFaint;
        ctx.fillText(msToClock(t), Math.min(Math.max(x, 14), cssW - 14), waveH + 14);
      }
    }

    // pen envelope
    const n = peaks.length;
    const envelope = (alphaFill: number, alphaStroke: number, clipToX?: number) => {
      ctx.save();
      if (clipToX !== undefined) {
        ctx.beginPath();
        ctx.rect(0, 0, clipToX, waveH);
        ctx.clip();
      }
      ctx.beginPath();
      for (let px = 0; px < cssW; px++) {
        const [, hi] = peaks[Math.min(n - 1, Math.floor((px / cssW) * n))];
        ctx.lineTo(px, midY - Math.max(0.6, hi * midY * 0.92));
      }
      for (let px = cssW - 1; px >= 0; px--) {
        const [lo] = peaks[Math.min(n - 1, Math.floor((px / cssW) * n))];
        ctx.lineTo(px, midY - Math.min(-0.6, lo * midY * 0.92));
      }
      ctx.closePath();
      ctx.globalAlpha = alphaFill;
      ctx.fillStyle = pen;
      ctx.fill();
      ctx.globalAlpha = alphaStroke;
      ctx.strokeStyle = pen;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();
      ctx.globalAlpha = 1;
    };

    const nowMs = (audioRef.current?.currentTime ?? 0) * 1000;
    const playX = xOf(Math.min(nowMs, durationMs));
    envelope(0.14, 0.4); // unplayed
    envelope(0.34, 0.85, playX); // played, stronger pen pressure

    // segment ticks along the chart base
    ctx.strokeStyle = ink;
    ctx.globalAlpha = 0.45;
    for (const s of segmentStartsMs) {
      const x = Math.round(xOf(s)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, waveH - 6);
      ctx.lineTo(x, waveH);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // event pen (playhead)
    ctx.strokeStyle = signal;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(playX, 0);
    ctx.lineTo(playX, waveH);
    ctx.stroke();
    ctx.fillStyle = signal;
    ctx.beginPath();
    ctx.moveTo(playX - 4, 0);
    ctx.lineTo(playX + 4, 0);
    ctx.lineTo(playX, 6);
    ctx.closePath();
    ctx.fill();

    // frame rule
    ctx.strokeStyle = rule;
    ctx.lineWidth = 1;
    ctx.strokeRect(0.5, 0.5, cssW - 1, waveH - 1);
  }, [peaks, durationMs, segmentStartsMs, audioRef]);

  // rAF redraw while playing; single redraws otherwise
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      draw();
      raf = requestAnimationFrame(loop);
    };
    if (playing) {
      raf = requestAnimationFrame(loop);
    } else {
      draw();
    }
    return () => cancelAnimationFrame(raf);
  }, [playing, draw, currentMs]);

  useEffect(() => {
    const obs = new ResizeObserver(() => draw());
    if (canvasRef.current) obs.observe(canvasRef.current);
    return () => obs.disconnect();
  }, [draw]);

  const msAtEvent = (e: React.PointerEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    return frac * durationMs;
  };

  return (
    <div className="chart">
      <div
        className="chart__frame"
        ref={frameRef}
        tabIndex={0}
        role="slider"
        aria-label="Recording position"
        aria-valuemin={0}
        aria-valuemax={Math.round(durationMs / 1000)}
        aria-valuenow={Math.round(currentMs / 1000)}
        onKeyDown={(e) => {
          if (e.key === " ") {
            e.preventDefault();
            onTogglePlay();
          } else if (e.key === "ArrowRight") {
            onSeek(Math.min(durationMs, currentMs + 5000));
          } else if (e.key === "ArrowLeft") {
            onSeek(Math.max(0, currentMs - 5000));
          }
        }}
      >
        <canvas
          ref={canvasRef}
          className="chart__canvas"
          onPointerDown={(e) => {
            draggingRef.current = true;
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
            onSeek(msAtEvent(e));
          }}
          onPointerMove={(e) => {
            const ms = msAtEvent(e);
            setHover({ x: e.clientX - canvasRef.current!.getBoundingClientRect().left, ms });
            if (draggingRef.current) onSeek(ms);
          }}
          onPointerUp={() => (draggingRef.current = false)}
          onPointerLeave={() => setHover(null)}
        />
        {hover && (
          <div className="chart__cursor-chip" style={{ left: hover.x }}>
            {msToClock(hover.ms)}
          </div>
        )}
      </div>
      <div className="transport">
        <button className="transport__play" onClick={onTogglePlay} aria-label={playing ? "Pause" : "Play"}>
          {playing ? "❚❚" : "▶"}
        </button>
        <span className="transport__time">
          <strong>{msToClock(currentMs)}</strong> / {msToClock(durationMs)}
        </span>
        <button className="transport__rate" onClick={onCycleRate} aria-label="Playback speed">
          {rate}×
        </button>
      </div>
    </div>
  );
}
