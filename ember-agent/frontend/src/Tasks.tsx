import { useEffect, useRef, useState } from "react";
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
]);
type Operation = z.infer<typeof operationSchema>;
const snapshot = handle.extend({
  result: z.string().nullable(),
  operation: operationSchema,
  build_artifact: z.boolean().default(false),
});
const notifications = z.object({
  revision: z.number().int(),
  items: z.array(snapshot).max(16),
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
  summaries,
  recall,
  delegation,
  build,
  connected,
  provider = "Agentic harness",
}: {
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
  } | null>(null);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [operation, setOperation] = useState<Operation>("inspect");
  const [port, setPort] = useState("");
  const [current, setCurrent] = useState<
    z.infer<typeof handle> & { operation: Operation }
  >();
  const [pending, setPending] = useState<
    z.infer<typeof notifications>["items"]
  >([]);
  const [confirmations, setConfirmations] = useState<
    z.infer<typeof confirmation>[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [recallResult, setRecallResult] = useState<Investigation>();
  const [receipt, setReceipt] = useState<
    z.infer<typeof receiptSchema> & { operation: "build" | "tool" }
  >();
  const taskId = current?.task_id;
  const isBuild = operation === "build";
  const servicePort = isBuild && port !== "" ? Number(port) : undefined;
  const validPort =
    servicePort === undefined ||
    (Number.isInteger(servicePort) &&
      servicePort >= 1024 &&
      servicePort <= 65535);
  const needsInference =
    operation === "summarize" || operation === "delegate" || isBuild;
  const canSubmit =
    !busy &&
    !!text.trim() &&
    !(connected && needsInference) &&
    (!isBuild || (build && validPort));
  useEffect(() => {
    if (!open) return;
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
        setPending(values.items);
        setConfirmations(
          await control(
            "task-confirmations",
            z.array(confirmation).max(16),
            undefined,
            controller.signal,
          ),
        );
        revision = values.revision;
        if (taskId) {
          const status = await control(
            "tasks/" + taskId,
            snapshot,
            undefined,
            controller.signal,
          );
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
  }, [open, taskId]);

  const submit = async () => {
    if (
      !retry.current ||
      retry.current.text !== text ||
      retry.current.operation !== operation ||
      retry.current.servicePort !== servicePort
    ) {
      retry.current = { key: createRequestId(), text, operation, servicePort };
    }
    setBusy(true);
    setError("");
    try {
      const submitted = await control("tasks", handle, {
        idempotency_key: retry.current.key,
        text,
        operation,
        ...(servicePort === undefined ? {} : { service_port: servicePort }),
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
      if (action === "acknowledge") {
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
  const inspectReceipt = async (id: string, operation: "build" | "tool") => {
    setReceipt(undefined);
    try {
      const value = await control(`tasks/${id}/receipt`, receiptSchema);
      setReceipt({ ...value, operation });
    } catch {
      setError("Receipt is unavailable.");
    }
  };
  const active =
    current &&
    [
      "queued",
      "starting",
      "running",
      "waiting_for_inference",
      "waiting_for_tool",
      "waiting_for_user",
    ].includes(current.state);
  return (
    <details
      className="controls"
      id="tasks"
      onToggle={(event) => {
        setOpen(event.currentTarget.open);
        if (!event.currentTarget.open) {
          setRecallResult(undefined);
          setReceipt(undefined);
        }
      }}
    >
      <summary>Tasks</summary>
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
              ? "Research request"
              : "Text to inspect"}
          <textarea
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
          </select>
        </label>
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
        {connected && needsInference && (
          <p>Disconnect from conversation to run this model task.</p>
        )}
        <button disabled={!canSubmit}>Queue task</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {open && <ActiveBuildService expected={build} />}
      {current && !pending.some((item) => item.task_id === current.task_id) && (
        <p role="status">
          {current.operation === "build" && "Build: "}
          {current.cancellation_requested && active
            ? "Cancellation requested"
            : current.state.replaceAll("_", " ")}
          {active && !current.cancellation_requested && (
            <button onClick={() => void act(current.task_id, "cancel")}>
              Cancel task
            </button>
          )}
        </p>
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
      {pending.map((item) => (
        <section key={item.task_id}>
          {item.operation === "build" ? (
            <>
              <h2>Build: {item.state.replaceAll("_", " ")}</h2>
              {item.result && <p>{item.result}</p>}
              {item.build_artifact && (
                <a href={`/api/tasks/${item.task_id}/build-artifact`}>
                  Download ZIP
                </a>
              )}
            </>
          ) : (
            <p>{item.result ?? item.state}</p>
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
          {(item.operation === "tool" || item.operation === "build") && (
            <button
              onClick={() =>
                void inspectReceipt(
                  item.task_id,
                  item.operation === "build" ? "build" : "tool",
                )
              }
            >
              View receipt
            </button>
          )}
          <button onClick={() => void act(item.task_id, "acknowledge")}>
            Dismiss
          </button>
        </section>
      ))}
      {open && recallResult && <Inspector result={recallResult} busy={false} />}
      {open && receipt && (
        <section
          aria-label={
            receipt.operation === "build" ? "Build receipt" : "Tool receipt"
          }
        >
          <p>Outcome: {receipt.state}</p>
          <pre>
            {receipt.receipt
              ? JSON.stringify(receipt.receipt, null, 2)
              : "No result data was retained for this outcome."}
          </pre>
        </section>
      )}
    </details>
  );
}
