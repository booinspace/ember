import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { control } from "./contracts";
import { createRequestId } from "./ids";

const discovery = z.object({
  servers: z
    .array(
      z.object({ id: z.string(), status: z.string(), tools: z.number().int() }),
    )
    .max(16),
  tools: z
    .array(
      z.object({
        id: z.string().max(64),
        server_id: z.string(),
        name: z.string(),
        description: z.string(),
        input_schema: z.record(z.string(), z.unknown()),
        requirements: z.array(z.string()),
        effect: z.enum(["read", "write", "denied"]),
        available: z.boolean(),
        reason: z.string().nullable(),
      }),
    )
    .max(1024),
});
const handle = z.object({
  task_id: z.string().uuid(),
  state: z.string(),
  cancellation_requested: z.boolean(),
});

export function Tools({
  active,
  showTasks,
}: {
  active: boolean;
  showTasks: () => void;
}) {
  const retry = useRef<{ signature: string; key: string } | null>(null);
  const [snapshot, setSnapshot] = useState<z.infer<typeof discovery>>();
  const [selected, setSelected] = useState("");
  const [argumentsText, setArguments] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [current, setCurrent] = useState<z.infer<typeof handle>>();
  const taskId = current?.task_id;
  const terminal = (state: string) =>
    ["succeeded", "failed", "cancelled", "timed_out", "uncertain"].includes(
      state,
    );
  const tool = snapshot?.tools.find((entry) => entry.id === selected);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void control("tools", discovery, undefined, controller.signal)
      .then(setSnapshot)
      .catch(() => {
        if (!controller.signal.aborted)
          setMessage("Tool availability could not be read.");
      });
    return () => controller.abort();
  }, [active]);
  useEffect(() => {
    if (!active || !taskId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const value = await control(
          "tasks/" + taskId,
          handle,
          undefined,
          controller.signal,
        );
        setCurrent(value);
        if (!terminal(value.state)) timer = setTimeout(poll, 500);
      } catch {
        if (!controller.signal.aborted) {
          setMessage("Tool status unavailable.");
          timer = setTimeout(poll, 1000);
        }
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, taskId]);
  const refresh = async () => {
    setBusy(true);
    try {
      setSnapshot(await control("tools/refresh", discovery, {}));
      setMessage("");
    } catch {
      setMessage("Tool discovery is unavailable.");
    } finally {
      setBusy(false);
    }
  };
  const invoke = async () => {
    if (!tool?.available) return;
    setBusy(true);
    try {
      const args: unknown = JSON.parse(argumentsText);
      const argumentsValue = z.record(z.string(), z.unknown()).parse(args);
      const signature = JSON.stringify([tool.id, argumentsValue]);
      if (retry.current?.signature !== signature)
        retry.current = { signature, key: createRequestId() };
      setCurrent(
        await control("tasks", handle, {
          idempotency_key: retry.current.key,
          text: "Run " + tool.name,
          operation: "tool",
          tool: { tool_id: tool.id, arguments: argumentsValue },
        }),
      );
      retry.current = null;
      setMessage(
        "Queued. Open Tasks for progress, any approval request, and the receipt.",
      );
    } catch {
      setMessage(
        "Tool request failed. Check the arguments and current availability.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="controls" aria-label="Run an MCP tool">
      <h3>Run an MCP tool</h3>
      <p>
        Only configured, granted tools can run. Write actions require approval
        in Tasks.
      </p>
      {snapshot?.servers.map((server) => (
        <p key={server.id}>
          {server.id}: {server.status} ({server.tools} tools)
        </p>
      ))}
      {snapshot?.servers.length === 0 && <p>No MCP servers configured.</p>}
      <button disabled={busy} onClick={() => void refresh()}>
        Refresh tools
      </button>
      <label htmlFor="mcp-tool-selector">Tool</label>
      <select
        id="mcp-tool-selector"
        value={selected}
        onChange={(event) => {
          setSelected(event.target.value);
          setArguments("{}");
          setMessage("");
        }}
      >
        <option value="">Choose a tool</option>
        {snapshot?.tools.map((entry) => (
          <option key={entry.id} value={entry.id} disabled={!entry.available}>
            {entry.server_id}: {entry.name} ({entry.effect})
            {entry.available ? "" : " — unavailable"}
          </option>
        ))}
      </select>
      {tool && (
        <>
          <p>{tool.description}</p>
          <p>
            {tool.requirements.join("; ")}
            {tool.reason ? ": " + tool.reason : ""}
          </p>
          <details>
            <summary>Required arguments</summary>
            <pre>{JSON.stringify(tool.input_schema, null, 2)}</pre>
          </details>
          <label>
            Arguments (JSON)
            <textarea
              maxLength={8192}
              value={argumentsText}
              onChange={(event) => setArguments(event.target.value)}
            />
          </label>
          <button
            disabled={busy || !tool.available}
            onClick={() => void invoke()}
          >
            Queue tool
          </button>
        </>
      )}
      {message && <p role="status">{message}</p>}
      {current && (
        <button type="button" onClick={showTasks}>
          Open Tasks
        </button>
      )}
      {current && (
        <p role="status">
          {current.cancellation_requested && !terminal(current.state)
            ? "Cancellation requested"
            : current.state}
          {!terminal(current.state) && !current.cancellation_requested && (
            <button
              onClick={() => {
                void control(
                  "tasks/" + current.task_id + "/cancel",
                  z.object({ accepted: z.boolean() }),
                  {},
                )
                  .then(() =>
                    setCurrent({ ...current, cancellation_requested: true }),
                  )
                  .catch(() =>
                    setMessage("Cancellation could not be recorded."),
                  );
              }}
            >
              Cancel tool
            </button>
          )}
        </p>
      )}
    </section>
  );
}
