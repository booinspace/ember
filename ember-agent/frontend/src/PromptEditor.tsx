import { useCallback, useEffect, useRef, useState } from "react";
import { control, promptDocuments, type PromptDocuments } from "./contracts";

type Kind = "system" | "personality";
const names = { system: "Shared system", personality: "This character" };
const errors: Record<string, string> = {
  prompt_settings_busy:
    "Wait for the current reply and speech to finish, then save again.",
  prompt_revision_conflict:
    "A saved document changed. Reload saved revisions to review your draft against it.",
  prompt_instruction_budget_exceeded:
    "Combined instructions exceed the displayed budget. Shorten the Markdown.",
  prompt_document_empty: "Both documents need some text.",
  prompt_file_missing:
    "A prompt file is missing. Restore its Markdown or write a replacement explicitly.",
  prompt_invalid_utf8:
    "A file contains invalid UTF-8. Replace its text with valid Markdown.",
};

function errorMessage(failure: unknown): string {
  const message =
    failure instanceof Error ? failure.message : "Prompt controls unavailable.";
  return errors[message] ?? message;
}

export function PromptEditor({
  enabled,
  busy,
}: {
  enabled: boolean;
  busy: boolean;
}) {
  const [documents, setDocuments] = useState<PromptDocuments | null>(null);
  const [drafts, setDrafts] = useState({ system: "", personality: "" });
  const [kind, setKind] = useState<Kind>("personality");
  const [preview, setPreview] = useState<PromptDocuments | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const active = useRef(false);

  const load = useCallback(async (keepDrafts = false): Promise<void> => {
    if (active.current) return;
    active.current = true;
    setPending(true);
    setError("");
    try {
      const value = await control("prompts", promptDocuments);
      setDocuments(value);
      if (!keepDrafts)
        setDrafts({
          system: value.system.markdown,
          personality: value.personality.markdown,
        });
      setPreview(null);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      active.current = false;
      setPending(false);
    }
  }, []);
  useEffect(() => {
    if (enabled && !documents) void load();
  }, [enabled, documents, load]);

  const submit = async (save: boolean): Promise<void> => {
    if (!documents || active.current || (save && busy)) return;
    active.current = true;
    setPending(true);
    setError("");
    setNotice("");
    try {
      const value = await control(
        save ? "prompts/" + kind : "prompts/preview",
        promptDocuments,
        {
          kind,
          markdown: drafts[kind],
          system_revision: documents.system.revision,
          personality_revision: documents.personality.revision,
        },
        undefined,
        save ? "PUT" : undefined,
      );
      setPreview(value);
      if (save) {
        setDocuments(value);
        setNotice("Saved. This version will be loaded for the next message.");
      }
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      active.current = false;
      setPending(false);
    }
  };
  const effective = preview ?? documents;
  const dirty = documents && drafts[kind] !== documents[kind].markdown;
  return (
    <div className="prompt-editor">
      {documents && (
        <>
          <label>
            Prompt scope
            <select
              value={kind}
              onChange={(event) => {
                setKind(
                  event.target.value === "system" ? "system" : "personality",
                );
                setPreview(null);
                setNotice("");
              }}
            >
              <option value="system">{names.system}</option>
              <option value="personality">{names.personality}</option>
            </select>
          </label>
          <div>
            <p>
              {kind === "system"
                ? "Affects all characters using this shared system file."
                : "Applies only to character " + documents.character_id + "."}
            </p>
            <label htmlFor="prompt-markdown">{names[kind]} Markdown</label>
            <textarea
              id="prompt-markdown"
              value={drafts[kind]}
              spellCheck={false}
              rows={8}
              onChange={(event) => {
                setDrafts((old) => ({ ...old, [kind]: event.target.value }));
                setPreview(null);
                setNotice("");
              }}
            />
            <p>
              {dirty ? "Unsaved draft." : "Saved Markdown."}{" "}
              {documents.applied_revision === documents.snapshot?.revision
                ? "Saved version applied to the latest observed message."
                : "Saved version loads at the next message."}
            </p>
            <div className="prompt-actions">
              <button
                disabled={pending || busy || !dirty}
                onClick={() => void submit(true)}
              >
                Save prompt
              </button>
              <button
                disabled={pending || !dirty}
                onClick={() => {
                  setDrafts((old) => ({
                    ...old,
                    [kind]: documents[kind].markdown,
                  }));
                  setPreview(null);
                  setError("");
                }}
              >
                Discard draft
              </button>
              <button disabled={pending} onClick={() => void submit(false)}>
                Preview draft
              </button>
              <button disabled={pending} onClick={() => void load(true)}>
                Reload saved revisions
              </button>
            </div>
            {busy && (
              <p>
                Save is available after the reply and speech finish. You can
                keep editing.
              </p>
            )}
          </div>
          <p>
            Combined instructions: {effective?.snapshot?.cost ?? "unavailable"}{" "}
            / {documents.instruction_limit} conservative units. The total prompt
            cap is unchanged.
          </p>
          <p>Preview uses the other document’s saved version.</p>
          {(effective?.error || documents[kind].error) && (
            <p role="alert">
              {errors[effective?.error ?? documents[kind].error ?? ""] ??
                effective?.error ??
                documents[kind].error}
            </p>
          )}
          <details>
            <summary>
              Effective system instruction{preview ? " (preview)" : " (saved)"}
            </summary>
            <pre>
              {effective?.snapshot?.instructions ??
                "Repair the documents to preview the effective instruction."}
            </pre>
          </details>
        </>
      )}
      {pending && <p role="status">Loading prompt controls…</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
      {!documents && !pending && (
        <button onClick={() => void load()}>Load prompts</button>
      )}
    </div>
  );
}
