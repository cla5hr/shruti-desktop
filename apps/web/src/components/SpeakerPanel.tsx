// Multi-pen legend: each diarized voice is a pen. Rename inline, merge duplicates,
// play a sample to identify who's who, or re-run detection entirely.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type SpeakerInfo } from "../api/client";

type Props = {
  meetingId: string;
  speakers: SpeakerInfo[];
  colorOf: (speakerId: string | null) => string;
  onPlaySample: (speakerId: string) => void;
};

function SpeakerRow({ meetingId, speaker, speakers, colorOf, onPlaySample }: Props & { speaker: SpeakerInfo }) {
  const [name, setName] = useState(speaker.display_name);
  const [mergeTarget, setMergeTarget] = useState("");
  const [error, setError] = useState("");
  const queryClient = useQueryClient();

  // resync when the name changes elsewhere (merge, re-run, another window)
  useEffect(() => {
    setName(speaker.display_name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [speaker.display_name]);

  const refresh = () => {
    setError("");
    queryClient.invalidateQueries({ queryKey: ["speakers", meetingId] });
    queryClient.invalidateQueries({ queryKey: ["transcript", meetingId] });
  };
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : "save failed");

  const rename = useMutation({
    mutationFn: () => api.renameSpeaker(speaker.id, name),
    onSuccess: refresh,
    onError: fail,
  });
  const merge = useMutation({
    mutationFn: () => api.mergeSpeaker(speaker.id, mergeTarget),
    onSuccess: refresh,
    onError: fail,
  });

  const others = speakers.filter((s) => s.id !== speaker.id);
  const save = () => {
    if (name.trim() && name.trim() !== speaker.display_name) rename.mutate();
  };

  return (
    <div className="speakers__row">
      <span className="nib" style={{ background: colorOf(speaker.id) }} />
      <input
        className="speakers__name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        aria-label={`Name for ${speaker.source_label}`}
      />
      <span className="speakers__count">
        {speaker.source_label} · {speaker.segment_count} segment{speaker.segment_count === 1 ? "" : "s"}
      </span>
      <button className="speakers__btn" onClick={() => onPlaySample(speaker.id)}>
        ▶ sample
      </button>
      {others.length > 0 && (
        <>
          <select
            className="speakers__select"
            value={mergeTarget}
            onChange={(e) => setMergeTarget(e.target.value)}
            aria-label="Merge into"
          >
            <option value="">merge into…</option>
            {others.map((o) => (
              <option key={o.id} value={o.id}>
                {o.display_name}
              </option>
            ))}
          </select>
          {mergeTarget && (
            <button className="speakers__btn speakers__btn--go" onClick={() => merge.mutate()}>
              Merge
            </button>
          )}
        </>
      )}
      {error && <span className="upload__error">{error}</span>}
    </div>
  );
}

export default function SpeakerPanel(props: Props) {
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  // per-meeting head-count for THIS re-run (empty = auto) — fixing one meeting's
  // split shouldn't require a trip to app-wide Settings
  const [people, setPeople] = useState("");
  const redetect = useMutation({
    mutationFn: () => {
      // empty field = use the Settings default; an explicit number (including 0
      // for "auto-detect") overrides Settings for this run only
      const n = Number.parseInt(people, 10);
      return api.rediarize(props.meetingId, Number.isFinite(n) && n >= 0 ? n : undefined);
    },
    onSuccess: () => {
      setConfirm(false);
      queryClient.invalidateQueries({ queryKey: ["meeting", props.meetingId] });
      queryClient.invalidateQueries({ queryKey: ["jobs", props.meetingId] });
    },
  });

  return (
    <div className="speakers">
      {props.speakers.length === 0 ? (
        <span className="tag">No speakers detected for this recording</span>
      ) : (
        props.speakers.map((s) => <SpeakerRow key={s.id} {...props} speaker={s} />)
      )}
      <div className="speakers__foot">
        <label className="speakers__people">
          People:
          <input
            className="speakers__name speakers__people-input"
            type="number"
            min={0}
            max={30}
            placeholder="auto"
            value={people}
            onChange={(e) => setPeople(e.target.value)}
            aria-label="How many people were in this meeting (empty = detect automatically)"
          />
        </label>
        {confirm ? (
          <button
            className="speakers__btn speakers__btn--go"
            onClick={() => redetect.mutate()}
            onBlur={() => setConfirm(false)}
          >
            Names reset — confirm re-detect?
          </button>
        ) : (
          <button className="speakers__btn" onClick={() => setConfirm(true)}>
            ↻ Re-detect speakers
          </button>
        )}
        <span className="speakers__hint">
          Wrong split? Type the real head-count in "People" and re-detect. Renames apply to
          the transcript instantly — hit Regenerate in Minutes to update names there.
        </span>
      </div>
    </div>
  );
}
