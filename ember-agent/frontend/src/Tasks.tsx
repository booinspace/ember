import { useEffect, useId, useRef, useState } from "react";
import { z } from "zod";
import {
  buildPreviewUrl,
  buildService,
  control,
  inspection,
  type Investigation,
} from "./contracts";
import { Inspector } from "./Inspector";
import { createRequestId } from "./ids";
import { capabilitySnapshot } from "./dashboard_contracts";

const handle = z.object({
  task_id: z.string().uuid(),
  state: z.string(),
  cancellation_requested: z.boolean(),
});
const operationSchema = z.enum([
  "inspect",
  "summarize",
  "recall",
  "review",
  "tool",
  "delegate",
  "build",
  "research",
]);
type Operation = z.infer<typeof operationSchema>;
const researchProviderSchema = z.enum(["exa", "parallel"]);
type ResearchProvider = z.infer<typeof researchProviderSchema>;
const researchProviderNames: Record<ResearchProvider, string> = {
  exa: "Exa",
  parallel: "Parallel",
};
const operationNames: Record<Operation, string> = {
  inspect: "Text inspection",
  summarize: "Summary",
  recall: "Deep recall",
  review: "Draft review",
  tool: "Tool",
  delegate: "Workspace inspection",
  build: "Build",
  research: "Web research",
};
type ReceiptOperation = Extract<Operation, "build" | "tool" | "research">;
const snapshot = handle.extend({
  result: z.string().nullable(),
  operation: operationSchema,
  build_artifact: z.boolean().default(false),
});
const notifications = z.object({
  revision: z.number().int(),
  items: z.array(snapshot).max(16),
  active: z.array(snapshot).max(33).default([]),
});
const acknowledged = z.object({ accepted: z.boolean() });
const confirmation = z.object({
  display_label: z.string().optional(),
  approvable: z.boolean().optional(),
  confirmation_id: z.string().uuid(),
  action_sha256: z.string(),
  tool: z.string(),
  arguments: z.record(z.string(), z.unknown()),
  state: z.string(),
});

const artifact = z.object({ task_id: z.string().uuid(), sha256: z.string() });
const researchSources = z
  .array(
    z.object({
      id: z.string().max(16),
      url: z
        .string()
        .max(512)
        .refine((value) => {
          if (!URL.canParse(value)) return false;
          const url = new URL(value);
          return (
            ["http:", "https:"].includes(url.protocol) &&
            !url.username &&
            !url.password
          );
        }),
      // Match Python's code-point limit, not JavaScript's UTF-16 string length.
      title: z.string().refine((value) => [...value].length <= 160),
      provider: z.string().max(32),
      operation: z.enum(["search", "read"]),
      retrieved_at: z.iso.datetime({ offset: true }),
      truncated: z.boolean(),
    }),
  )
  .max(12);
const receiptSchema = z.object({
  task_id: z.string().uuid(),
  state: z.string(),
  receipt: z
    .object({
      tool: z.string(),
      outcome: z.string(),
      code: z.string().nullable(),
      data: z.record(z.string(), z.unknown()),
    })
    .nullable(),
});

function ActiveBuildService({ expected }: { expected: boolean }) {
  const [service, setService] = useState<z.infer<typeof buildService> | null>(
    null,
  );
  const [statusError, setStatusError] = useState(false);
  const [stopError, setStopError] = useState(false);
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    if (stopping) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      try {
        const value = await control(
          "tasks/build-service",
          buildService.nullable(),
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setService(value);
          setStatusError(false);
        }
      } catch {
        if (!controller.signal.aborted) setStatusError(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 1000);
      }
    };
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [stopping]);

  const stop = async (id: string) => {
    setStopping(true);
    setStopError(false);
    try {
      await control(`tasks/${id}/stop-service`, acknowledged, {});
      setService(null);
    } catch {
      setStopError(true);
    } finally {
      setStopping(false);
    }
  };

  if (!service)
    return expected && statusError ? (
      <p role="status">Build preview status unavailable.</p>
    ) : null;
  const url = buildPreviewUrl.safeParse(service.url);
  return (
    <section aria-label="Active Build service">
      <h2>Active Build service</h2>
      <p>Preview URL works only in a browser on the Ember host.</p>
      {!statusError && url.success ? (
        <p>
          <a href={url.data} target="_blank" rel="noopener noreferrer">
            {url.data}
          </a>
        </p>
      ) : (
        <p role="status">Build preview URL unavailable.</p>
      )}
      <p>Expires at {new Date(service.expires_at * 1000).toLocaleString()}.</p>
      {stopError && <p role="alert">Could not stop Build service.</p>}
      <button disabled={stopping} onClick={() => void stop(service.task_id)}>
        Stop service
      </button>
    </section>
  );
}

export function Tasks({
  visible,
  requestedTaskId,
  clearRequestedTask,
  summaries,
  recall,
  delegation,
  build,
  connected,
  provider = "Agentic harness",
}: {
  visible: boolean;
  requestedTaskId: string | null;
  clearRequestedTask: () => void;
  summaries: boolean;
  recall: boolean;
  delegation: boolean;
  build: boolean;
  connected: boolean;
  provider?: string;
}) {
  const retry = useRef<{
    key: string;
    text: string;
    operation: Operation;
    servicePort: number | undefined;
    researchProvider: ResearchProvider | undefined;
  } | null>(null);
  const [text, setText] = useState("");
  const [operation, setOperation] = useState<Operation>("inspect");
  const [hostedProvider, setHostedProvider] = useState<ResearchProvider>();
  const researchHelpId = useId();
  const [port, setPort] = useState("");
  const [current, setCurrent] = useState<
    z.infer<typeof handle> & { operation: Operation }
  >();
  const [pending, setPending] = useState<z.infer<typeof snapshot>[]>([]);
  const [activeTasks, setActiveTasks] = useState<z.infer<typeof snapshot>[]>(
    [],
  );
  const [requestedTask, setRequestedTask] =
    useState<z.infer<typeof snapshot>>();
  const [confirmations, setConfirmations] = useState<
    z.infer<typeof confirmation>[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [recallResult, setRecallResult] = useState<Investigation>();
  const [receipt, setReceipt] = useState<
    z.infer<typeof receiptSchema> & { operation: ReceiptOperation }
  >();
  const taskId = requestedTaskId ?? current?.task_id;
  const isBuild = operation === "build";
  const isResearch = operation === "research";
  const researchProvider = isResearch ? hostedProvider : undefined;
  const servicePort = isBuild && port !== "" ? Number(port) : undefined;
  const validPort =
    servicePort === undefined ||
    (Number.isInteger(servicePort) &&
      servicePort >= 1024 &&
      servicePort <= 65535);
  const waitsForRealtime = operation === "delegate" || isResearch || isBuild;
  const needsDisconnect = connected && operation === "summarize";
  const canSubmit =
    !busy &&
    !!text.trim() &&
    !needsDisconnect &&
    (!isBuild || (build && validPort)) &&
    (!isResearch || researchProvider !== undefined);
  useEffect(() => {
    setHostedProvider(undefined);
    if (!visible) return;
    const abort = new AbortController();
    void control("capabilities", capabilitySnapshot, undefined, abort.signal)
      .then(({ capabilities }) => {
        if (abort.signal.aborted) return;
        const research = capabilities.find(
          (entry) =>
            entry.id === "harness.research" &&
            entry.status === "enabled" &&
            entry.operations.includes("research"),
        );
        setHostedProvider(
          researchProviderSchema.safeParse(research?.provider).data,
        );
      })
      .catch(() => {
        if (!abort.signal.aborted) setHostedProvider(undefined);
      });
    return () => abort.abort();
  }, [visible]);
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let revision: number | undefined;
    const refresh = async () => {
      try {
        const values = await control(
          "task-notifications" +
            (revision === undefined ? "" : "?revision=" + revision),
          notifications,
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setPending(values.items);
        setActiveTasks(values.active);
        const decisions = await control(
          "task-confirmations",
          z.array(confirmation).max(16),
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setConfirmations(decisions);
        revision = values.revision;
        if (
          taskId &&
          (!requestedTaskId ||
            ![...values.active, ...values.items].some(
              (item) => item.task_id === taskId,
            ))
        ) {
          const status = await control(
            "tasks/" + taskId,
            snapshot,
            undefined,
            controller.signal,
          );
          if (controller.signal.aborted) return;
          if (requestedTaskId) setRequestedTask(status);
          else
            setCurrent((value) => (value?.task_id === taskId ? status : value));
        }
      } catch {
        if (!controller.signal.aborted) setError("Task status unavailable.");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 500);
      }
    };
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [visible, taskId, requestedTaskId]);

  const submit = async () => {
    if (
      !retry.current ||
      retry.current.text !== text ||
      retry.current.operation !== operation ||
      retry.current.servicePort !== servicePort ||
      retry.current.researchProvider !== researchProvider
    ) {
      retry.current = {
        key: createRequestId(),
        text,
        operation,
        servicePort,
        researchProvider,
      };
    }
    setBusy(true);
    setError("");
    try {
      const submitted = await control("tasks", handle, {
        idempotency_key: retry.current.key,
        text,
        operation,
        ...(servicePort === undefined ? {} : { service_port: servicePort }),
        ...(researchProvider === undefined
          ? {}
          : { research_provider: researchProvider }),
      });
      setCurrent({ ...submitted, operation });
      retry.current = null;
      setText("");
      if (isBuild) setPort("");
    } catch {
      setError("Task submission failed.");
    } finally {
      setBusy(false);
    }
  };
  const act = async (id: string, action: "cancel" | "acknowledge") => {
    try {
      await control("tasks/" + id + "/" + action, acknowledged, {});
      if (action === "cancel") {
        setActiveTasks((items) =>
          items.map((item) =>
            item.task_id === id
              ? { ...item, cancellation_requested: true }
              : item,
          ),
        );
        setCurrent((value) =>
          value?.task_id === id
            ? { ...value, cancellation_requested: true }
            : value,
        );
        setRequestedTask((value) =>
          value?.task_id === id
            ? { ...value, cancellation_requested: true }
            : value,
        );
      } else {
        if (id === requestedTaskId) clearRequestedTask();
        setPending((items) => items.filter((item) => item.task_id !== id));
        setCurrent((value) => (value?.task_id === id ? undefined : value));
        setReceipt((value) => (value?.task_id === id ? undefined : value));
      }
    } catch {
      setError("Task update failed.");
    }
  };
  const decide = async (
    item: z.infer<typeof confirmation>,
    approve: boolean,
  ) => {
    try {
      await control(
        "task-confirmations/" + item.confirmation_id,
        confirmation,
        {
          action_sha256: item.action_sha256,
          approve,
        },
      );
      setConfirmations((items) =>
        items.filter((value) => value.confirmation_id !== item.confirmation_id),
      );
    } catch {
      setError("Confirmation unavailable; refresh the task status.");
    }
  };
  const download = async (id: string) => {
    setError("");
    try {
      await control("tasks/" + id + "/artifact", artifact, {});
      const link = document.createElement("a");
      link.href = "/api/tasks/" + id + "/artifact";
      link.download = "ember-task.txt";
      link.click();
    } catch {
      setError("Task artifact unavailable.");
    }
  };
  const inspectRecall = async (id: string) => {
    setRecallResult(undefined);
    setError("");
    try {
      const response = await control("tasks/" + id + "/recall", inspection);
      if (response.result) setRecallResult(response.result);
      else setError("Recall has expired or is no longer available.");
    } catch {
      setError("Recall unavailable.");
    }
  };
  const inspectReceipt = async (id: string, operation: ReceiptOperation) => {
    setReceipt(undefined);
    try {
      const value = await control(`tasks/${id}/receipt`, receiptSchema);
      setReceipt({ ...value, operation });
    } catch {
      setError("Receipt is unavailable.");
    }
  };
  const isActive = (state: string) =>
    [
      "queued",
      "starting",
      "running",
      "waiting_for_inference",
      "waiting_for_realtime",
      "waiting_for_tool",
      "waiting_for_user",
    ].includes(state);
  const results = [...pending];
  const finished = new Set(results.map((item) => item.task_id));
  const progress: (z.infer<typeof handle> & { operation: Operation })[] =
    activeTasks.filter((item) => !finished.has(item.task_id));
  if (requestedTask && requestedTask.task_id === requestedTaskId) {
    if (
      !finished.has(requestedTaskId) &&
      !progress.some((item) => item.task_id === requestedTaskId)
    ) {
      if (isActive(requestedTask.state)) progress.unshift(requestedTask);
      else {
        results.unshift(requestedTask);
        finished.add(requestedTaskId);
      }
    }
  }
  if (
    current &&
    !finished.has(current.task_id) &&
    !progress.some((item) => item.task_id === current.task_id)
  )
    progress.unshift(current);
  const selectedTaskVisible =
    !!requestedTaskId &&
    (finished.has(requestedTaskId) ||
      progress.some((item) => item.task_id === requestedTaskId));
  useEffect(() => {
    if (visible && selectedTaskVisible)
      document.getElementById(`task-${requestedTaskId}`)?.focus();
  }, [visible, selectedTaskVisible, requestedTaskId]);
  const sources = researchSources.safeParse(receipt?.receipt?.data.sources);
  return (
    <section
      className="controls tasks-panel"
      aria-label="Task queue and results"
    >
      <h3>Queue work</h3>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) void submit();
        }}
      >
        <label>
          {isBuild
            ? "Build request"
            : operation === "delegate"
              ? "Workspace inspection request"
              : isResearch
                ? "Public web research question"
                : "Text to inspect"}
          <textarea
            aria-describedby={isResearch ? researchHelpId : undefined}
            maxLength={8192}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        <label>
          Operation
          <select
            value={operation}
            onChange={(event) =>
              setOperation(operationSchema.parse(event.target.value))
            }
          >
            <option value="inspect">Count words and characters</option>
            {summaries && <option value="summarize">Summarize</option>}
            {recall && <option value="recall">Recall deeply</option>}
            {delegation && (
              <option value="delegate">
                Inspect with {provider} (read-only)
              </option>
            )}
            {build && <option value="build">Build with {provider}</option>}
            <option value="review">Review a draft</option>
            {(hostedProvider || isResearch) && (
              <option value="research" disabled={!hostedProvider}>
                Research the public web
              </option>
            )}
          </select>
        </label>
        {isResearch && (
          <p id={researchHelpId}>
            {researchProvider ? (
              <>
                Use public information only. The hosted service{" "}
                <strong>{researchProviderNames[researchProvider]}</strong>{" "}
                receives your approved search queries and any public URLs read,
                including URLs from search results. Put each exact query on its
                own Search: line and each approved URL on a line by itself.
              </>
            ) : (
              "Web research needs an available hosted provider (Exa or Parallel)."
            )}
          </p>
        )}
        {isBuild && (
          <>
            <p>Use synthetic, non-sensitive requests and files only.</p>
            <label>
              Local service port (optional)
              <input
                type="number"
                min={1024}
                max={65535}
                value={port}
                onChange={(event) => setPort(event.target.value)}
              />
            </label>
            <p>Leave blank to build without starting a local preview.</p>
          </>
        )}
        {needsDisconnect && (
          <p>Disconnect from conversation to run this model task.</p>
        )}
        {connected && waitsForRealtime && (
          <p>
            This task can queue now. It starts after conversation disconnects.
          </p>
        )}
        <button disabled={!canSubmit}>Queue task</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {visible && <ActiveBuildService expected={build} />}
      {progress.length > 0 && (
        <section aria-label="Task progress" className="task-progress">
          <h3>Task progress</h3>
          {progress.map((item) => (
            <div
              key={item.task_id}
              id={`task-${item.task_id}`}
              tabIndex={-1}
              role="group"
              aria-label={`${operationNames[item.operation]} task ${item.task_id.slice(0, 8)}`}
            >
              <p role="status">
                {operationNames[item.operation]}:{" "}
                {item.cancellation_requested && isActive(item.state)
                  ? "Cancellation requested"
                  : item.state === "waiting_for_realtime"
                    ? "Waiting for conversation to disconnect"
                    : item.state.replaceAll("_", " ")}
              </p>
              <small>Task {item.task_id.slice(0, 8)}</small>
              {isActive(item.state) && !item.cancellation_requested && (
                <button
                  aria-label={`Cancel task: ${operationNames[item.operation]} ${item.task_id.slice(0, 8)}`}
                  onClick={() => void act(item.task_id, "cancel")}
                >
                  Cancel task
                </button>
              )}
            </div>
          ))}
        </section>
      )}
      {confirmations.map((item) => (
        <section key={item.confirmation_id}>
          <p>Approve {item.display_label || item.tool.replaceAll("_", " ")}?</p>
          <pre>{JSON.stringify(item.arguments, null, 2)}</pre>
          <button
            disabled={
              item.approvable === false ||
              (item.tool.startsWith("mcp.") && !item.display_label)
            }
            onClick={() => void decide(item, true)}
          >
            Approve
          </button>
          <button onClick={() => void decide(item, false)}>Reject</button>
        </section>
      ))}
      {results.map((item) => {
        const itemOperation = item.operation;
        return (
          <section
            key={item.task_id}
            id={`task-${item.task_id}`}
            tabIndex={-1}
            aria-label={`${operationNames[item.operation]} task ${item.task_id.slice(0, 8)}`}
          >
            <h3>
              {operationNames[item.operation]}:{" "}
              {item.state.replaceAll("_", " ")}
            </h3>
            <small>Task {item.task_id.slice(0, 8)}</small>
            {item.result && <p className="task-result">{item.result}</p>}
            {item.operation === "build" && item.build_artifact && (
              <a href={`/api/tasks/${item.task_id}/build-artifact`}>
                Download ZIP
              </a>
            )}
            {item.operation !== "recall" &&
              item.operation !== "build" &&
              item.operation !== "tool" &&
              item.state === "succeeded" && (
                <button onClick={() => void download(item.task_id)}>
                  Save result
                </button>
              )}
            {item.operation === "recall" && item.state === "succeeded" && (
              <button onClick={() => void inspectRecall(item.task_id)}>
                View recall
              </button>
            )}
            {(itemOperation === "tool" ||
              itemOperation === "build" ||
              itemOperation === "research") && (
              <button
                onClick={() => void inspectReceipt(item.task_id, itemOperation)}
              >
                View receipt
              </button>
            )}
            <button onClick={() => void act(item.task_id, "acknowledge")}>
              Dismiss
            </button>
          </section>
        );
      })}
      {visible && recallResult && (
        <Inspector result={recallResult} busy={false} />
      )}
      {visible && receipt && (
        <section aria-label={`${operationNames[receipt.operation]} receipt`}>
          <p>Outcome: {receipt.state}</p>
          {receipt.operation === "research" &&
            sources.success &&
            sources.data.length > 0 && (
              <>
                <h3>Retrieved sources</h3>
                <ul className="research-sources">
                  {sources.data.map((source) => (
                    <li key={source.id}>
                      <span>[{source.id}] </span>
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {source.title || source.url}
                      </a>
                      <small>
                        {" "}
                        · {source.provider} ·{" "}
                        {source.operation === "read"
                          ? "Page excerpts"
                          : "Search excerpts"}
                        {source.truncated ? " · Truncated" : ""}
                      </small>
                    </li>
                  ))}
                </ul>
              </>
            )}
          {receipt.operation === "research" &&
            (!sources.success || sources.data.length === 0) && (
              <p>No source data was retained for this outcome.</p>
            )}
          {receipt.operation !== "research" && (
            <pre>
              {receipt.receipt
                ? JSON.stringify(receipt.receipt, null, 2)
                : "No result data was retained for this outcome."}
            </pre>
          )}
        </section>
      )}
    </section>
  );
}
