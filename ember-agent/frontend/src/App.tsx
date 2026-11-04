import { Fragment } from "react";
import { Skills } from "./Skills";
import {
  useDashboard,
  SystemStatus,
  ActivityCard,
  Capabilities,
  PromptInspector,
} from "./Dashboard";
import { createRequestId } from "./ids";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import type { CharacterConnection } from "./connection";
import type { TextConnection } from "./text_connection";
import { activate } from "./contracts";
import {
  control,
  inspection,
  turnInspection,
  speechOptions,
  type SpeechSelection,
  type Investigation,
  type RuntimeState,
  type TurnDetails,
} from "./contracts";
import { Controls } from "./controls";
import { Tasks } from "./Tasks";
import { Tools } from "./Tools";
import { Inspector } from "./Inspector";
import { TurnDetailsPanel } from "./TurnDetails";
import { PromptEditor } from "./PromptEditor";
import { VoiceControls } from "./VoiceControls";

type Turn = {
  id: string;
  role: "user" | "assistant";
  text: string;
  requestId?: string;
};
const acknowledgement = z.object({
  request_id: z.string().uuid(),
  status: z.string(),
});

export function App() {
  const [state, setState] = useState<RuntimeState | null>(null);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [microphone, setMicrophone] = useState(false);
  const [speechTarget, setSpeechTarget] = useState<string | null>(null);
  const [stoppingSpeech, setStoppingSpeech] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState("");
  const [deepStatus, setDeepStatus] = useState("");
  const [lanLink, setLanLink] = useState("");
  const microphoneAvailable =
    globalThis.isSecureContext && Boolean(navigator.mediaDevices?.getUserMedia);
  const [result, setResult] = useState<Investigation | null>(null);
  const [details, setDetails] = useState<TurnDetails | null>(null);
  const monitor = useDashboard(state !== null);
  const [view, setView] = useState<"conversation" | "activity">("conversation");
  const [inspectDetails, setInspectDetails] = useState(false);
  const [requestedInspection, setRequestedInspection] = useState<string | null>(
    null,
  );
  const [inspectionNonce, setInspectionNonce] = useState(0);
  const inspectRequest = (identity: string): void => {
    setRequestedInspection(identity);
    setInspectionNonce((value) => value + 1);
  };
  const [thinkingSaving, setThinkingSaving] = useState(false);
  const thinkingPending = useRef(false);
  const messages = useRef<HTMLDivElement | null>(null);
  const following = useRef(true);
  const forceScroll = useRef(false);
  const sending = useRef(false);
  const connection = useRef<CharacterConnection | TextConnection | null>(null);
  const outputId = useRef<string | null>(null);
  const investigationId = useRef<string | null>(null);
  const mounted = useRef(true);
  const polling = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    mounted.current = true;
    void activate()
      .then((value) => {
        if (mounted.current) setState(value);
      })
      .catch(() => {
        if (mounted.current)
          setError("Open the private activation link created by ember start.");
      });
    return () => {
      mounted.current = false;
      if (polling.current) clearTimeout(polling.current);
      void connection.current?.close().catch(() => undefined);
      if (investigationId.current) {
        void control(
          "deep/" + investigationId.current + "/cancel",
          z.object({ cancelled: z.boolean() }),
          {},
        ).catch(() => undefined);
      }
    };
  }, []);

  useEffect(() => {
    if (!connected) {
      setDetails(null);
      return;
    }
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const inspect = async (): Promise<void> => {
      try {
        const value = await control(
          "turn-details?metadata_only=" +
            String(!inspectDetails && !state?.thinking_enabled),
          turnInspection,
          undefined,
          AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]),
        );
        if (!abort.signal.aborted) setDetails(value.turn);
      } catch {
        if (!abort.signal.aborted) setDetails(null);
      } finally {
        if (!abort.signal.aborted)
          timer = setTimeout(() => void inspect(), 500);
      }
    };
    void inspect();
    return () => {
      abort.abort();
      if (timer) clearTimeout(timer);
    };
  }, [connected, inspectDetails, state?.thinking_enabled]);

  useEffect(() => {
    if (
      details?.request_id === speechTarget &&
      (details.stage === "complete" || details.stage === "interrupted")
    )
      setSpeechTarget(null);
  }, [details, speechTarget]);

  useEffect(() => {
    if (messages.current && (forceScroll.current || following.current)) {
      messages.current.scrollTo({
        top: messages.current.scrollHeight,
        behavior: "auto",
      });
      following.current = true;
    }
    forceScroll.current = false;
  }, [turns]);

  const report = (value: string): void => {
    if (mounted.current) setError(value);
  };
  const append = (
    text: string,
    segment: string,
    role: "user" | "assistant",
  ): void => {
    if (!mounted.current) return;
    if (role === "user") forceScroll.current = true;
    const id = role + ":" + segment;
    setTurns((old) => {
      const last = old.at(-1);
      if (last?.id === id)
        return [...old.slice(0, -1), { ...last, text: last.text + text }];
      return [...old.slice(-23), { id, role, text }];
    });
  };
  const shareLan = async (): Promise<void> => {
    try {
      const link = await control(
        "lan-link",
        z.object({ url: z.string().url().max(1024) }),
        {},
      );
      setLanLink(link.url);
      if (navigator.clipboard) await navigator.clipboard.writeText(link.url);
    } catch {
      report(
        "Could not copy the LAN link. Use the private activation file on the host.",
      );
    }
  };
  const connect = async (): Promise<void> => {
    if (!state || busy) return;
    setBusy(true);
    setError("");
    try {
      if (connected) {
        await connection.current?.close();
        connection.current = null;
        setConnected(false);
        setMicrophone(false);
        setSpeechTarget(null);
      } else {
        if (!mounted.current) return;
        const callbacks = [
          append,
          (message?: string) =>
            report(message ?? "Reply delivery was interrupted."),
          () => {
            if (!mounted.current) return;
            connection.current = null;
            setConnected(false);
            setMicrophone(false);
            setSpeechTarget(null);
            setBusy(false);
            report("Conversation disconnected; reconnect to continue.");
          },
        ] as const;
        const client =
          state.transport === "http"
            ? new (await import("./text_connection")).TextConnection(
                ...callbacks,
              )
            : new (await import("./connection")).CharacterConnection(
                ...callbacks,
                setSpeechTarget,
              );
        connection.current = client;
        await client.connect(state);
        if (!mounted.current) {
          await client.close();
          return;
        }
        setConnected(true);
      }
    } catch (failure: unknown) {
      await connection.current?.close().catch(() => undefined);
      connection.current = null;
      setConnected(false);
      setMicrophone(false);
      setError(
        failure instanceof Error
          ? failure.message
          : "Room connection is unavailable. Restart Ember and reconnect.",
      );
    } finally {
      setBusy(false);
    }
  };
  const send = async (): Promise<void> => {
    if (
      !connection.current ||
      !connected ||
      busy ||
      sending.current ||
      thinkingPending.current ||
      !input.trim()
    )
      return;
    sending.current = true;
    forceScroll.current = true;
    const text = input.trim();
    setInput("");
    setError("");
    setBusy(true);
    outputId.current = null;
    const requestId = createRequestId();
    setTurns((old) => [
      ...old.slice(-23),
      { id: "user:" + requestId, role: "user", text, requestId },
    ]);
    try {
      await connection.current.ask(text, requestId);
    } catch (failure: unknown) {
      report(failure instanceof Error ? failure.message : "Turn unavailable.");
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };
  const toggleThinking = async (enabled: boolean): Promise<void> => {
    if (!state || thinkingPending.current) return;
    const previous = state.thinking_enabled;
    thinkingPending.current = true;
    setThinkingSaving(true);
    setState((old) => (old ? { ...old, thinking_enabled: enabled } : old));
    try {
      const value = await control(
        "thinking",
        z.object({ enabled: z.boolean() }),
        { enabled },
      );
      setState((old) =>
        old ? { ...old, thinking_enabled: value.enabled } : old,
      );
    } catch {
      if (mounted.current)
        setState((old) => (old ? { ...old, thinking_enabled: previous } : old));
      report("Thinking is unavailable for this runtime.");
    } finally {
      thinkingPending.current = false;
      if (mounted.current) setThinkingSaving(false);
    }
  };
  const changeSpeech = async (selection: SpeechSelection): Promise<void> => {
    setBusy(true);
    try {
      const speech = await control("speech", speechOptions, selection);
      if (mounted.current) setState((old) => (old ? { ...old, speech } : old));
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const poll = async (id: string): Promise<void> => {
    if (!mounted.current || investigationId.current !== id) return;
    try {
      const value = await control("deep/" + id, inspection);
      if (!mounted.current || investigationId.current !== id) return;
      setDeepStatus(value.status.replaceAll("_", " "));
      if (value.status === "running") {
        polling.current = setTimeout(() => void poll(id), 750);
      } else {
        setResult(value.result);
        investigationId.current = null;
        if (!value.result)
          report(
            "Recall is unavailable, cancelled, expired or invalidated. Try a narrower query.",
          );
      }
    } catch {
      investigationId.current = null;
      report(
        "Recall inspection is unavailable. No result was added to the conversation.",
      );
    }
  };
  const deeply = async (): Promise<void> => {
    if (!state || !input.trim() || investigationId.current) return;
    setError("");
    setResult(null);
    const id = createRequestId();
    investigationId.current = id;
    setDeepStatus("working");
    try {
      await control("deep", acknowledgement, {
        request_id: id,
        text: input.trim(),
      });
      await poll(id);
    } catch {
      investigationId.current = null;
      setDeepStatus("unavailable");
      report("Deep recall could not start. Ordinary chat is still available.");
    }
  };
  const cancel = async (): Promise<void> => {
    const id = investigationId.current;
    if (!id) return;
    if (polling.current) clearTimeout(polling.current);
    try {
      const reply = await control(
        "deep/" + id + "/cancel",
        z.object({ cancelled: z.boolean() }),
        {},
      );
      if (!mounted.current || investigationId.current !== id) return;
      if (reply.cancelled) {
        investigationId.current = null;
        setDeepStatus("cancelled");
      } else {
        await poll(id);
      }
    } catch {
      report("Cancellation could not be confirmed; checking recall status.");
      await poll(id);
    }
  };
  const answer = async (): Promise<void> => {
    if (!result || !connected || busy) return;
    setBusy(true);
    outputId.current = null;
    try {
      await connection.current?.answerInvestigation(
        result.request_id,
        result.query,
      );
    } catch {
      report("This recall can no longer be used. Refresh the investigation.");
      setResult(null);
    } finally {
      setBusy(false);
    }
  };
  const toggleMicrophone = async (): Promise<void> => {
    try {
      await connection.current?.microphone(!microphone);
      setMicrophone(!microphone);
    } catch {
      report(
        "Microphone or speech recognition is unavailable; text chat remains available.",
      );
    }
  };
  const stopSpeaking = async (): Promise<void> => {
    const client = connection.current;
    if (
      !client ||
      !("stopSpeaking" in client) ||
      !speechTarget ||
      stoppingSpeech === speechTarget
    )
      return;
    const target = speechTarget;
    setStoppingSpeech(target);
    try {
      await client.stopSpeaking();
    } catch {
      report(
        "Audio is stopped locally. Server interruption could not be verified.",
      );
    } finally {
      if (mounted.current)
        setStoppingSpeech((old) => (old === target ? null : old));
    }
  };
  return (
    <main className="dashboard-shell">
      <header className="app-header">
        <div className="mark" aria-hidden="true">
          e
        </div>
        <div>
          <h1>Ember</h1>
          <p>LOCAL CHARACTER SYSTEM</p>
        </div>
        <span className="status" role="status">
          {connected ? "Connected" : "Disconnected"}
        </span>
        <button
          disabled={!state || !state.text_available || busy}
          onClick={() => void connect()}
        >
          {connected ? "Disconnect" : "Connect"}
        </button>
      </header>
      <SystemStatus snapshot={monitor.snapshot} online={monitor.online} />
      {state?.transport === "http" && (
        <p role="status">
          {state.text_available
            ? "Voice is unavailable. Text chat and memory controls are ready."
            : "Conversation is unavailable. Memory controls are ready."}
        </p>
      )}
      {state?.lan_available && (
        <div className="lan-sharing">
          <button onClick={() => void shareLan()}>Copy LAN link</button>
          {lanLink && (
            <label>
              Private LAN access link{" "}
              <input
                readOnly
                value={lanLink}
                onFocus={(event) => event.target.select()}
              />
            </label>
          )}
        </div>
      )}
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      <div className="layout workspace">
        <nav className="workspace-nav" aria-label="Dashboard navigation">
          <span className="eyebrow">WORKSPACE</span>
          <button
            className={view === "conversation" ? "selected" : ""}
            onClick={() => setView("conversation")}
          >
            <span aria-hidden="true">◉</span> Conversation
          </button>
          <button
            className={view === "activity" ? "selected" : ""}
            onClick={() => setView("activity")}
          >
            <span aria-hidden="true">↗</span> Activity{" "}
            <span className="nav-count">{monitor.events.length}</span>
          </button>
          <a href="#inference-context">
            <span aria-hidden="true">✦</span> Inference context
          </a>
          <a href="#capabilities">
            <span aria-hidden="true">◇</span> Capabilities
          </a>
          <a href="#skills">
            <span aria-hidden="true">▧</span> Skills
          </a>
          <a href="#memory-controls">
            <span aria-hidden="true">▤</span> Memory & settings
          </a>
          <div className="runtime-card">
            <i className={monitor.online ? "live-dot" : "offline-dot"} />
            <strong>
              {monitor.online ? "Runtime online" : "Connecting to runtime"}
            </strong>
            <p>{monitor.snapshot?.model ?? "Local model"}</p>
            <small>
              {monitor.snapshot?.system.gpu_name ?? "Local hardware"}
            </small>
          </div>
        </nav>
        <section className="conversation" aria-label="Conversation">
          <div className="conversation-toolbar">
            <div>
              <span className="eyebrow">
                {view === "conversation"
                  ? "CHARACTER SESSION"
                  : "RUNTIME TRANSACTIONS"}
              </span>
              <h2>{view === "conversation" ? "Conversation" : "Activity"}</h2>
            </div>
            <div
              className="view-switch"
              role="group"
              aria-label="Conversation view"
            >
              <button
                className={view === "conversation" ? "selected" : ""}
                onClick={() => setView("conversation")}
              >
                Chat
              </button>
              <button
                className={view === "activity" ? "selected" : ""}
                onClick={() => setView("activity")}
              >
                Activity
              </button>
            </div>
          </div>
          <div
            className="messages"
            aria-live="polite"
            ref={messages}
            onScroll={(event) => {
              const element = event.currentTarget;
              following.current =
                element.scrollHeight -
                  element.scrollTop -
                  element.clientHeight <
                80;
            }}
          >
            {view === "conversation" && turns.length === 0 && (
              <div className="welcome">
                <div className="welcome-emblem" aria-hidden="true">
                  ✦
                </div>
                <span className="eyebrow">YOUR LOCAL AI WORKSPACE</span>
                <h2>Start a conversation.</h2>
                <p>
                  Talk, search, or explore a memory. Tool calls and context
                  activity appear here as they happen.
                </p>
              </div>
            )}
            {view === "conversation" &&
              turns.map((turn) => (
                <Fragment key={turn.id}>
                  <article className={"turn " + turn.role}>
                    <span>{turn.role === "user" ? "You" : "Ember"}</span>
                    <p>{turn.text}</p>
                  </article>
                  {turn.role === "user" &&
                    monitor.events
                      .filter(
                        (event) =>
                          event.request_id &&
                          event.request_id === turn.requestId &&
                          event.kind !== "inference" &&
                          event.kind !== "system",
                      )
                      .map((event) => (
                        <ActivityCard
                          key={event.id}
                          event={event}
                          inspect={inspectRequest}
                        />
                      ))}
                </Fragment>
              ))}
            {monitor.events
              .filter(
                (event) =>
                  view === "activity" ||
                  (["task", "tool", "memory"].includes(event.kind) &&
                    !turns.some((turn) => turn.requestId === event.request_id)),
              )
              .slice(-64)
              .map((event) => (
                <ActivityCard
                  key={event.id}
                  event={event}
                  inspect={inspectRequest}
                />
              ))}
            {view === "activity" && monitor.events.length === 0 && (
              <div className="welcome">
                <h2>Activity will appear here.</h2>
                <p>
                  Memory recall, tools, tasks and inference share one live
                  timeline.
                </p>
              </div>
            )}
          </div>
          <TurnDetailsPanel
            details={details}
            inspect={() => setInspectDetails(true)}
          />
          {state?.speech && (
            <VoiceControls
              options={state.speech}
              busy={
                busy ||
                Boolean(
                  details &&
                  [
                    "preparing",
                    "generating",
                    "thinking",
                    "answering",
                    "awaiting_delivery",
                  ].includes(details.stage),
                )
              }
              change={changeSpeech}
            />
          )}
          <form
            className="composer"
            aria-label="Message Ember"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <label className="sr-only" htmlFor="prompt">
              Message Ember
            </label>
            <textarea
              id="prompt"
              maxLength={1600}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing &&
                  event.nativeEvent.keyCode !== 229
                ) {
                  event.preventDefault();
                  if (!event.repeat) void send();
                }
              }}
              placeholder="Message Ember…"
            />
            <div className="actions">
              <button
                type="submit"
                disabled={!connected || busy || thinkingSaving || !input.trim()}
              >
                Send
              </button>
              <label className="thinking-toggle">
                <input
                  type="checkbox"
                  checked={state?.thinking_enabled ?? false}
                  disabled={
                    !state?.thinking_available || busy || thinkingSaving
                  }
                  onChange={(event) =>
                    void toggleThinking(event.target.checked)
                  }
                />
                Thinking
              </label>
              <button
                type="button"
                disabled={
                  !state || !input.trim() || investigationId.current !== null
                }
                onClick={() => void deeply()}
              >
                Recall deeply
              </button>
              {investigationId.current && (
                <button type="button" onClick={() => void cancel()}>
                  Cancel recall
                </button>
              )}
              <button
                type="button"
                aria-pressed={microphone}
                disabled={
                  !connected || !state?.voice || !microphoneAvailable || busy
                }
                onClick={() => void toggleMicrophone()}
              >
                {microphone ? "Mute microphone" : "Use microphone"}
              </button>
              {state?.voice && state.transport === "livekit" && (
                <button
                  type="button"
                  disabled={
                    !connected ||
                    !speechTarget ||
                    stoppingSpeech === speechTarget
                  }
                  onClick={() => void stopSpeaking()}
                >
                  {stoppingSpeech === speechTarget && speechTarget
                    ? "Stopping speech…"
                    : "Stop speaking"}
                </button>
              )}
            </div>
            <p className="composer-hint">
              Enter to send · Shift+Enter for a new line
            </p>
            {thinkingSaving && (
              <p className="quiet" role="status">
                Updating Thinking…
              </p>
            )}
            {deepStatus && (
              <p className="coverage" role="status">
                Deep recall: {deepStatus}
              </p>
            )}
            {!connected && (
              <p className="quiet" role="status">
                {state
                  ? "Connect above to enable Send."
                  : "Waiting for Ember activation…"}
              </p>
            )}
            {state?.voice && !microphoneAvailable && (
              <p className="hint">
                Microphone input on another device requires HTTPS. Text chat and
                spoken replies are available.
              </p>
            )}
            {!state?.voice && (
              <p className="quiet">
                Voice is unavailable until local speech services are ready.
              </p>
            )}
          </form>
          {result && (
            <Inspector
              result={result}
              answer={() => void answer()}
              busy={busy || !connected}
            />
          )}
        </section>
        <div className="dashboard-side">
          <PromptInspector
            snapshot={monitor.snapshot}
            requestedId={requestedInspection}
            nonce={inspectionNonce}
          />
          <Capabilities enabled={state !== null} />
          <Skills
            enabled={state !== null}
            useSkill={(name) => {
              setInput("Use " + name + " to ");
              document.getElementById("prompt")?.focus();
            }}
          />
          {state?.tasks_available && <Tools />}
          {state?.tasks_available && (
            <Tasks
              summaries={state.summary_tasks_available}
              recall={state.recall_tasks_available}
              delegation={state.delegation_available}
              connected={connected}
              provider={state.delegation_provider ?? "Agentic harness"}
            />
          )}
          {state?.prompts_available && (
            <PromptEditor
              busy={
                busy ||
                Boolean(
                  details &&
                  [
                    "preparing",
                    "generating",
                    "thinking",
                    "answering",
                    "awaiting_delivery",
                  ].includes(details.stage),
                )
              }
            />
          )}
          {state && (
            <Controls
              state={state}
              update={(value) => {
                setState(value);
                setResult(null);
              }}
              failure={report}
            />
          )}{" "}
        </div>
      </div>
    </main>
  );
}
