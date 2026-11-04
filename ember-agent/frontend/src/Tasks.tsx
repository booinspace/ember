import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { control, inspection, type Investigation } from "./contracts";
import { Inspector } from "./Inspector";
import { createRequestId } from "./ids";

const handle = z.object({
  task_id: z.string().uuid(),
  state: z.string(),
  cancellation_requested: z.boolean(),
});
const operationSchema = z.enum(["inspect", "summarize", "recall", "review"]);
const snapshot = handle.extend({
  result: z.string().nullable(),
  operation: operationSchema,
});
const notifications = z.object({
  revision: z.number().int(),
  items: z.array(snapshot).max(16),
});
const acknowledged = z.object({ accepted: z.boolean() });
const confirmation = z.object({
  confirmation_id: z.string().uuid(),
  action_sha256: z.string(),
  tool: z.string(),
  arguments: z.record(z.string(), z.unknown()),
  state: z.string(),
});

const artifact = z.object({ task_id: z.string().uuid(), sha256: z.string() });

export function Tasks({
  summaries,
  recall,
  connected,
}: {
  summaries: boolean;
  recall: boolean;
  connected: boolean;
}) {
  const retry = useRef<{ key: string; text: string; operation: string } | null>(
    null,
  );
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [operation, setOperation] =
    useState<z.infer<typeof operationSchema>>("inspect");
  const [current, setCurrent] = useState<z.infer<typeof handle>>();
  const [pending, setPending] = useState<
    z.infer<typeof notifications>["items"]
  >([]);
  const [confirmations, setConfirmations] = useState<
    z.infer<typeof confirmation>[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [recallResult, setRecallResult] = useState<Investigation>();
  const taskId = current?.task_id;
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
          setCurrent(status);
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
      retry.current.operation !== operation
    ) {
      retry.current = { key: createRequestId(), text, operation };
    }
    setBusy(true);
    setError("");
    try {
      setCurrent(
        await control("tasks", handle, {
          idempotency_key: retry.current.key,
          text,
          operation,
        }),
      );
      retry.current = null;
      setText("");
    } catch {
      setError("Task submission failed.");
    } finally {
      setBusy(false);
    }
  };
  const act = async (id: string, action: "cancel" | "acknowledge") => {
    try {
      await control("tasks/" + id + "/" + action, acknowledged, {});
      if (action === "acknowledge")
        setPending((items) => items.filter((item) => item.task_id !== id));
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
      onToggle={(event) => {
        setOpen(event.currentTarget.open);
        if (!event.currentTarget.open) setRecallResult(undefined);
      }}
    >
      <summary>Tasks</summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && text.trim() && !(connected && operation === "summarize"))
            void submit();
        }}
      >
        <label>
          Text to inspect
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
            <option value="review">Review a draft</option>
          </select>
        </label>
        {connected && operation === "summarize" && (
          <p>Disconnect from conversation to run a summary.</p>
        )}
        <button
          disabled={
            busy || !text.trim() || (connected && operation === "summarize")
          }
        >
          Queue task
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {current && (
        <p role="status">
          {current.cancellation_requested && active
            ? "Cancellation requested"
            : current.state}
          {active && !current.cancellation_requested && (
            <button onClick={() => void act(current.task_id, "cancel")}>
              Cancel task
            </button>
          )}
        </p>
      )}
      {confirmations.map((item) => (
        <section key={item.confirmation_id}>
          <p>Approve {item.tool.replaceAll("_", " ")}?</p>
          <pre>{JSON.stringify(item.arguments, null, 2)}</pre>
          <button onClick={() => void decide(item, true)}>Approve</button>
          <button onClick={() => void decide(item, false)}>Reject</button>
        </section>
      ))}
      {pending.map((item) => (
        <p key={item.task_id}>
          {item.result ?? item.state}
          {item.operation !== "recall" && item.state === "succeeded" && (
            <button onClick={() => void download(item.task_id)}>
              Save result
            </button>
          )}
          {item.operation === "recall" && item.state === "succeeded" && (
            <button onClick={() => void inspectRecall(item.task_id)}>
              View recall
            </button>
          )}
          <button onClick={() => void act(item.task_id, "acknowledge")}>
            Dismiss
          </button>
        </p>
      ))}
      {open && recallResult && <Inspector result={recallResult} busy={false} />}
    </details>
  );
}
