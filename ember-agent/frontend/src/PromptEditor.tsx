import { useRef, useState } from "react";
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

export function PromptEditor({ busy }: { busy: boolean }) {
  const [documents, setDocuments] = useState<PromptDocuments | null>(null);
  const [drafts, setDrafts] = useState({ system: "", personality: "" });
  const [kind, setKind] = useState<Kind>("personality");
  const [preview, setPreview] = useState<PromptDocuments | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const active = useRef(false);
  const showError = (failure: unknown): void => {
    const message =
      failure instanceof Error
        ? failure.message
        : "Prompt controls unavailable.";
    setError(errors[message] ?? message);
  };
  const load = async (keepDrafts = false): Promise<void> => {
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
      showError(failure);
    } finally {
      active.current = false;
      setPending(false);
    }
  };
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
      showError(failure);
    } finally {
      active.current = false;
      setPending(false);
    }
  };
  const effective = preview ?? documents;
  const dirty = documents && drafts[kind] !== documents[kind].markdown;
  return (
    <details
      className="prompt-editor"
      onToggle={(event) => {
        if (event.currentTarget.open && !documents) void load();
      }}
    >
      <summary>Prompts</summary>
      {documents && (
        <>
          <div role="tablist" aria-label="Prompt scope">
            {(["system", "personality"] as const).map((value) => (
              <button
                key={value}
                id={"prompt-tab-" + value}
                role="tab"
                aria-selected={kind === value}
                tabIndex={kind === value ? 0 : -1}
                onKeyDown={(event) => {
                  if (
                    !["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                      event.key,
                    )
                  )
                    return;
                  event.preventDefault();
                  const next =
                    event.key === "Home"
                      ? "system"
                      : event.key === "End"
                        ? "personality"
                        : kind === "system"
                          ? "personality"
                          : "system";
                  setKind(next);
                  setPreview(null);
                  document.getElementById("prompt-tab-" + next)?.focus();
                }}
                aria-controls="prompt-panel"
                onClick={() => {
                  setKind(value);
                  setPreview(null);
                  setNotice("");
                }}
              >
                {names[value]}
              </button>
            ))}
          </div>
          <div
            id="prompt-panel"
            role="tabpanel"
            aria-labelledby={"prompt-tab-" + kind}
          >
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
    </details>
  );
}
