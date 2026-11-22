import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Skills } from "./Skills";
import {
  useDashboard,
  SystemStatus,
  ActivityCard,
  Capabilities,
  PromptInspector,
} from "./Dashboard";
import { createRequestId } from "./ids";
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
import {
  WorkspacePanel,
  WorkspaceTabs,
  workspacePanel,
  type WorkspacePage,
} from "./Workspace";

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
  const [page, setPage] = useState<WorkspacePage>("conversation");
  const [inspectDetails, setInspectDetails] = useState(false);
  const [requestedInspection, setRequestedInspection] = useState<string | null>(
    null,
  );
  const [requestedTaskId, setRequestedTaskId] = useState<string | null>(null);
  const focusTarget = useRef<string | null>(null);
  const selectPage = (next: WorkspacePage, focus?: string): void => {
    if (next === page) return;
    focusTarget.current = focus ?? null;
    setRequestedInspection(null);
    setRequestedTaskId(null);
    setPage(next);
  };
  const inspectRequest = (identity: string): void => {
    selectPage("inference", workspacePanel("inference"));
    setRequestedInspection(identity);
  };
  const showTasks = (taskId: string): void => {
    selectPage("tasks", workspacePanel("tasks"));
    setRequestedTaskId(taskId);
  };
  useLayoutEffect(() => {
    if (focusTarget.current) {
      document.getElementById(focusTarget.current)?.focus();
      focusTarget.current = null;
    }
  }, [page]);
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

  useLayoutEffect(() => {
    if (page !== "conversation") return;
    if (messages.current && (forceScroll.current || following.current)) {
      messages.current.scrollTo({
        top: messages.current.scrollHeight,
        behavior: "auto",
      });
      following.current = true;
    }
    forceScroll.current = false;
  }, [page, turns]);

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
  const replyInProgress =
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
    );
  const voiceActions = (
    <>
      {(page === "conversation" || microphone) && (
        <button
          type="button"
          aria-pressed={microphone}
          disabled={
            !connected ||
            !state?.voice ||
            !microphoneAvailable ||
            (busy && !microphone)
          }
          onClick={() => void toggleMicrophone()}
        >
          {microphone ? "Mute microphone" : "Use microphone"}
        </button>
      )}
      {state?.voice &&
        state.transport === "livekit" &&
        (page === "conversation" || speechTarget) && (
          <button
            type="button"
            disabled={
              !connected || !speechTarget || stoppingSpeech === speechTarget
            }
            onClick={() => void stopSpeaking()}
          >
            {stoppingSpeech === speechTarget && speechTarget
              ? "Stopping speech…"
              : "Stop speaking"}
          </button>
        )}
    </>
  );
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
        {page !== "conversation" && (microphone || speechTarget) && (
          <div
            className="session-actions"
            aria-label="Active voice controls"
            role="group"
          >
            {voiceActions}
          </div>
        )}
      </header>
      <WorkspaceTabs page={page} select={selectPage} />
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      <WorkspacePanel
        view="conversation"
        page={page}
        description={
          state?.transport === "http" && (
            <p role="status">
              {state.text_available
                ? "Voice is unavailable. Text chat is ready."
                : "Conversation is unavailable. Memory controls are ready."}
            </p>
          )
        }
      >
        <div className="conversation" aria-label="Conversation">
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
            {turns.length === 0 && (
              <div className="welcome">
                <div className="welcome-emblem" aria-hidden="true">
                  ✦
                </div>
                <span className="eyebrow">YOUR LOCAL AI WORKSPACE</span>
                <h3>Start a conversation.</h3>
                <p>
                  Connect to chat with Ember. Explore related tool calls and
                  memory context as you go.
                </p>
              </div>
            )}
            {turns.map((turn) => (
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
                        showTasks={showTasks}
                      />
                    ))}
              </Fragment>
            ))}
          </div>
          <TurnDetailsPanel
            details={details}
            inspect={() => setInspectDetails(true)}
          />
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
              {page === "conversation" && voiceActions}
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
        </div>
      </WorkspacePanel>
      <WorkspacePanel
        view="activity"
        page={page}
        description={
          <p>
            Runtime health and recent memory, tool, task, and inference
            activity.
          </p>
        }
      >
        <SystemStatus snapshot={monitor.snapshot} online={monitor.online} />
        <section aria-label="Recent activity" className="activity-timeline">
          <h3>Recent activity</h3>
          {monitor.events.length === 0 && (
            <p className="page-empty">No activity yet.</p>
          )}
          {monitor.events.slice(-64).map((event) => (
            <ActivityCard
              key={event.id}
              event={event}
              inspect={inspectRequest}
              showTasks={showTasks}
            />
          ))}
        </section>
      </WorkspacePanel>
      <WorkspacePanel
        view="inference"
        page={page}
        description={<p>Inspect assembled inputs for recent model requests.</p>}
      >
        {page === "inference" && (
          <PromptInspector
            snapshot={monitor.snapshot}
            requestedId={requestedInspection}
          />
        )}
      </WorkspacePanel>
      <WorkspacePanel
        view="memory"
        page={page}
        description={
          <p>
            Choose what Ember may recall and whether it learns from
            conversations.
          </p>
        }
      >
        {state ? (
          <div className="page-stack">
            <Controls
              state={state}
              update={(value) => {
                setState(value);
                setResult(null);
              }}
              failure={report}
            />
            {state.lan_available && (
              <section className="controls" aria-label="LAN access">
                <h3>LAN access</h3>
                <button onClick={() => void shareLan()}>Copy LAN link</button>
                {lanLink && (
                  <label>
                    Private LAN access link
                    <input
                      readOnly
                      value={lanLink}
                      onFocus={(event) => event.target.select()}
                    />
                  </label>
                )}
              </section>
            )}
          </div>
        ) : (
          <p className="page-empty">Waiting for Ember activation…</p>
        )}
      </WorkspacePanel>
      <WorkspacePanel
        view="prompts"
        page={page}
        description={
          <p>Edit the shared system and character Markdown instructions.</p>
        }
      >
        {state?.prompts_available ? (
          <PromptEditor enabled={page === "prompts"} busy={replyInProgress} />
        ) : (
          <p className="page-empty">
            {state
              ? "Prompt editing is unavailable for this runtime."
              : "Waiting for Ember activation…"}
          </p>
        )}
      </WorkspacePanel>
      <WorkspacePanel
        view="tasks"
        page={page}
        description={
          <p>Queue work, review approvals, and check results and receipts.</p>
        }
      >
        {state?.tasks_available ? (
          <Tasks
            visible={page === "tasks"}
            requestedTaskId={requestedTaskId}
            clearRequestedTask={() => setRequestedTaskId(null)}
            summaries={state.summary_tasks_available}
            recall={state.recall_tasks_available}
            delegation={state.delegation_available}
            build={state.build_available}
            connected={connected}
            provider={state.delegation_provider ?? "Agentic harness"}
          />
        ) : (
          <p className="page-empty">
            {state
              ? "Tasks are unavailable for this runtime."
              : "Waiting for Ember activation…"}
          </p>
        )}
      </WorkspacePanel>
      <WorkspacePanel
        view="mcp"
        page={page}
        description={
          <p>
            Run configured, granted tools. Write actions require approval in
            Tasks.
          </p>
        }
      >
        {state?.tasks_available ? (
          <Tools active={page === "mcp"} showTasks={showTasks} />
        ) : (
          <p className="page-empty">
            {state
              ? "MCP tool execution is unavailable for this runtime."
              : "Waiting for Ember activation…"}
          </p>
        )}
      </WorkspacePanel>
      <WorkspacePanel
        view="skills"
        page={page}
        description={<p>Browse, select, and manage portable skill packages.</p>}
      >
        {state ? (
          <Skills
            enabled={page === "skills"}
            useSkill={(name) => {
              setInput("Use " + name + " to ");
              selectPage("conversation", "prompt");
            }}
          />
        ) : (
          <p className="page-empty">Waiting for Ember activation…</p>
        )}
      </WorkspacePanel>
      <WorkspacePanel
        view="providers"
        page={page}
        description={<p>Check tool availability and provider status.</p>}
      >
        {state ? (
          <Capabilities enabled={page === "providers"} />
        ) : (
          <p className="page-empty">Waiting for Ember activation…</p>
        )}
      </WorkspacePanel>
      <WorkspacePanel view="voice" page={page}>
        {state?.speech ? (
          <VoiceControls
            options={state.speech}
            busy={replyInProgress}
            change={changeSpeech}
          />
        ) : (
          <p className="page-empty">
            {state
              ? "Voice settings are unavailable for this runtime."
              : "Waiting for Ember activation…"}
          </p>
        )}
      </WorkspacePanel>
    </main>
  );
}
