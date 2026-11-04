import { useEffect, useRef, useState } from "react";
import type { SpeechOptions, SpeechSelection } from "./contracts";

export function VoiceControls({
  options,
  busy,
  change,
}: {
  options: SpeechOptions;
  busy: boolean;
  change: (selection: SpeechSelection) => Promise<void>;
}) {
  const [voice, setVoice] = useState(options.selection.voice);
  const [speed, setSpeed] = useState(options.selection.speed);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  useEffect(() => {
    setVoice(options.selection.voice);
    setSpeed(options.selection.speed);
  }, [options.selection.voice, options.selection.speed]);
  const save = async (): Promise<void> => {
    if (busy || pending.current) return;
    pending.current = true;
    setSaving(true);
    setError("");
    try {
      await change({ voice, speed });
    } catch {
      setError(
        "Voice settings were not changed. Wait for the reply to finish and try again.",
      );
    } finally {
      pending.current = false;
      setSaving(false);
    }
  };
  return (
    <details className="voice-controls">
      <summary>Voice and speaking speed</summary>
      <p>Voice model: {options.model} · CPU · Text appears ahead of speech.</p>
      <fieldset disabled={busy || saving}>
        <legend className="sr-only">Speech preferences</legend>
        <label htmlFor="speech-voice">Voice</label>
        <select
          id="speech-voice"
          value={voice}
          onChange={(event) => setVoice(event.target.value)}
        >
          {options.voices.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <label htmlFor="speech-speed">
          Speaking speed: {speed.toFixed(2)}×
        </label>
        <input
          id="speech-speed"
          type="range"
          min="0.75"
          max="1.5"
          step="0.05"
          value={speed}
          onChange={(event) => setSpeed(Number(event.target.value))}
        />
        <button
          type="button"
          disabled={
            voice === options.selection.voice &&
            speed === options.selection.speed
          }
          onClick={() => void save()}
        >
          {saving ? "Applying…" : "Apply voice"}
        </button>
      </fieldset>
      <p>
        Applies to the next reply in this Ember session. Speed is approximate;
        settings reset on restart.
      </p>
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
