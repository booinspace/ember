import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { control } from "./contracts";
import {
  dashboardSnapshot,
  promptInspection,
  capabilitySnapshot,
  mergeActivity,
  percent,
  duration,
  type ActivityEvent,
  type DashboardSnapshot,
  type Capability,
} from "./dashboard_contracts";

export function useDashboard(enabled: boolean) {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [online, setOnline] = useState(false);
  const cursor = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (document.visibilityState === "hidden") {
        timer = setTimeout(() => void poll(), 3000);
        return;
      }
      try {
        const value = await control(
          "dashboard?after=" + cursor.current,
          dashboardSnapshot,
          undefined,
          AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]),
        );
        if (abort.signal.aborted) return;
        setSnapshot(value);
        setOnline(true);
        const reset = value.revision < cursor.current;
        setEvents((old) => mergeActivity(reset ? [] : old, value.events));
        cursor.current = value.revision;
      } catch {
        if (!abort.signal.aborted) setOnline(false);
      }
      if (!abort.signal.aborted) timer = setTimeout(() => void poll(), 1000);
    };
    void poll();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [enabled]);
  return { snapshot, events, online };
}
export function SystemStatus({
  snapshot,
  online,
}: {
  snapshot: DashboardSnapshot | null;
  online: boolean;
}) {
  const s = snapshot?.system;
  const last = snapshot?.requests.at(-1);
  const gateway = snapshot?.gateway;
  const queue = gateway
    ? gateway.queued_p1 +
      gateway.queued_p2 +
      gateway.queued_p3 +
      gateway.queued_p4
    : null;
  return (
    <section className="telemetry-strip" aria-label="System monitoring">
      <div className="metric">
        <span className="metric-label">
          GATEWAY <i className={online ? "live-dot" : "offline-dot"} />
        </span>
        <strong>{gateway?.status.replaceAll("_", " ") ?? "Connecting"}</strong>
        <small>
          {snapshot?.backend ?? "Local inference"} ·{" "}
          {queue == null ? "— queued" : `${queue} queued`}
        </small>
      </div>
      <div className="metric">
        <span className="metric-label">CPU</span>
        <strong>{percent(s?.cpu_percent)}</strong>
        <meter
          min="0"
          max="100"
          value={s?.cpu_percent ?? 0}
          aria-label="CPU load"
        />
        <small>Host utilization</small>
      </div>
      <div className="metric">
        <span className="metric-label">MEMORY</span>
        <strong>{percent(s?.memory_percent)}</strong>
        <meter
          min="0"
          max="100"
          value={s?.memory_percent ?? 0}
          aria-label="RAM load"
        />
        <small>
          {s?.memory_used_bytes == null
            ? "Awaiting host sample"
            : `${(s.memory_used_bytes / 1073741824).toFixed(1)} / ${((s.memory_total_bytes ?? 0) / 1073741824).toFixed(1)} GiB`}
        </small>
      </div>
      <div className="metric">
        <span className="metric-label">GPU / VRAM</span>
        <strong>{percent(s?.gpu_percent)}</strong>
        <small>
          {s?.gpu_used_mib == null
            ? "GPU sensor unavailable"
            : `${(s.gpu_used_mib / 1024).toFixed(1)} / ${((s.gpu_total_mib ?? 0) / 1024).toFixed(1)} GiB`}
        </small>
      </div>
      <div className="metric">
        <span className="metric-label">INPUT BUDGET</span>
        <strong>
          {last?.input_units == null
            ? "—"
            : ((last.input_units / last.input_budget_units) * 100).toFixed(0) +
              "%"}
        </strong>
        <small>
          {last?.input_units == null
            ? "Awaiting character input"
            : `${last.input_units.toLocaleString()} / ${last.input_budget_units.toLocaleString()} guard units`}
        </small>
      </div>
      <div className="metric">
        <span className="metric-label">DECODE</span>
        <strong>
          {snapshot?.usage?.tokens_per_second?.toFixed(1) ?? "—"}
          <em> tok/s</em>
        </strong>
        <small>
          {snapshot?.usage?.first_visible_seconds == null
            ? "First token pending"
            : `${(snapshot.usage.first_visible_seconds * 1000).toFixed(0)} ms to visible text`}
        </small>
      </div>
    </section>
  );
}
const status: Record<ActivityEvent["status"], string> = {
  running: "Running",
  waiting_for_model: "Waiting for model",
  waiting_for_realtime: "Waiting for inference access",
  waiting_for_tool: "Waiting for tool",
  waiting_for_user: "Approval needed",
  succeeded: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
  timed_out: "Timed out",
  uncertain: "Outcome uncertain",
};
const icons = {
  tool: "↗",
  memory: "◇",
  task: "▤",
  inference: "✦",
  system: "◉",
};
export function ActivityCard({
  event,
  inspect,
  showTasks,
}: {
  event: ActivityEvent;
  inspect: (id: string) => void;
  showTasks: (taskId: string) => void;
}) {
  const { task_id: taskId } = event;
  const label = status[event.status];
  const active =
    event.status === "running" || event.status.startsWith("waiting_");
  const name =
    event.name === "web_search"
      ? "Web search"
      : event.name === "read_page"
        ? "Read public page"
        : event.name === "ember_skill_view"
          ? "Load skill"
          : event.kind === "memory"
            ? "Memory & context"
            : event.name.replaceAll("_", " ");
  return (
    <details
      className={`activity-card ${event.kind} ${active ? "is-active" : ""}`}
    >
      <summary>
        <span className="activity-icon" aria-hidden="true">
          {icons[event.kind]}
        </span>
        <span className="activity-name">{name}</span>
        <span className={`event-status ${event.status}`}>{label}</span>
        <time>{active ? "Live" : duration(event.duration_ms)}</time>
        <span className="chevron" aria-hidden="true">
          ⌄
        </span>
      </summary>
      <div className="activity-body">
        <p>
          {event.kind === "memory"
            ? active
              ? "Assembling authorized memory and session context…"
              : `${event.count ?? 0} memory values included in this turn.`
            : `Provider: ${event.provider} · ${label}`}
        </p>
        {event.code && (
          <p className="quiet">
            Diagnostic: <code>{event.code}</code>
          </p>
        )}
        {event.kind === "task" && event.status === "waiting_for_model" && (
          <p className="quiet">Waiting for an inference slot.</p>
        )}
        {event.status === "uncertain" && (
          <p>
            Check the task receipt before another action. This operation will
            not be repeated automatically.
          </p>
        )}
        <div className="transaction-actions">
          {event.request_id && (
            <button
              className="text-button"
              type="button"
              onClick={() => inspect(event.request_id!)}
            >
              Inspect request
            </button>
          )}
          {taskId && (
            <button
              className="text-button"
              type="button"
              onClick={() => showTasks(taskId)}
            >
              Task progress & receipt
            </button>
          )}
          <small>{event.id.slice(0, 8)}</small>
        </div>
      </div>
    </details>
  );
}
export function Capabilities({ enabled }: { enabled: boolean }) {
  const [items, setItems] = useState<Capability[] | null>(null);
  const [filter, setFilter] = useState("all");
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    void control("capabilities", capabilitySnapshot, undefined, abort.signal)
      .then((value) => setItems(value.capabilities))
      .catch(() => undefined);
    return () => abort.abort();
  }, [enabled]);
  const refresh = async () => {
    try {
      await control("tools/refresh", z.object({}).passthrough(), {});
      const value = await control("capabilities", capabilitySnapshot);
      setItems(value.capabilities);
    } catch {
      setItems(null);
    }
  };
  return (
    <section
      className="dashboard-panel capabilities-panel"
      aria-label="Capabilities"
      id="capabilities"
    >
      <div className="panel-heading">
        <label>
          Capability category
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">All</option>
            <option value="native">Native</option>
            <option value="public_search">Search</option>
            <option value="mcp">MCP</option>
            <option value="harness">Harness</option>
          </select>
        </label>
        <button onClick={() => void refresh()}>Refresh capabilities</button>
      </div>
      <div className="capability-list">
        {items === null && (
          <p className="quiet">Capability status is connecting…</p>
        )}
        {items
          ?.filter((item) => filter === "all" || item.kind === filter)
          .map((item) => (
            <details className="capability" key={item.id}>
              <summary>
                <i
                  className={
                    item.status === "enabled" ? "live-dot" : "offline-dot"
                  }
                />
                <span>
                  {item.name}
                  <small>{item.provider}</small>
                </span>
                <span className={`capability-state ${item.status}`}>
                  {item.status}
                </span>
              </summary>
              <div className="capability-detail">
                <p>
                  {item.reason ??
                    "Available through the scoped tool/task broker."}
                </p>
                <small>
                  {item.operations.join(" · ")}
                  {item.effect ? ` · ${item.effect}` : ""}
                </small>
              </div>
            </details>
          ))}
      </div>
    </section>
  );
}
export function PromptInspector({
  snapshot,
  requestedId,
}: {
  snapshot: DashboardSnapshot | null;
  requestedId: string | null;
}) {
  const [inspecting, setInspecting] = useState(requestedId !== null);
  const [selection, setSelection] = useState(requestedId ?? "latest");
  const [value, setValue] = useState<z.infer<typeof promptInspection> | null>(
    null,
  );
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const requests = snapshot?.requests ?? [];
  const identity =
    selection === "latest"
      ? requests.at(-1)?.request_id
      : requests.find((entry) => entry.request_id === selection)?.request_id;
  useEffect(() => {
    setValue(null);
    setCopied(false);
    setError("");
    if (!inspecting || !identity) {
      if (selection !== "latest" && !identity)
        setError(
          "This request expired or was replaced. Choose Latest request.",
        );
      return;
    }
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    void control(
      "inference/requests/" + identity,
      promptInspection,
      undefined,
      abort.signal,
    )
      .then((result) => {
        if (abort.signal.aborted) return;
        setValue(result);
        setError("");
        const validate = async () => {
          try {
            await control(
              "inference/requests/" + identity + "?validate_only=true",
              z.object({ valid: z.literal(true) }),
              undefined,
              abort.signal,
            );
          } catch {
            if (!abort.signal.aborted) {
              setValue(null);
              setError("This request is no longer available for inspection.");
            }
            return;
          }
          if (!abort.signal.aborted)
            timer = setTimeout(() => void validate(), 3000);
        };
        timer = setTimeout(() => void validate(), 3000);
      })
      .catch(() => {
        if (!abort.signal.aborted)
          setError("This request is no longer available for inspection.");
      });
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [inspecting, identity, selection]);
  return (
    <div className="controls prompt-inspector">
      <p className="quiet">
        Exact input captured before inference. Loaded on demand and kept in
        memory only.
      </p>
      <label>
        Model request
        <select
          value={selection}
          onChange={(event) => setSelection(event.target.value)}
        >
          <option value="latest">Latest request</option>
          {requests.map((request) => (
            <option key={request.request_id} value={request.request_id}>
              {request.workload} · {request.request_id.slice(0, 8)} ·{" "}
              {request.messages} messages
            </option>
          ))}
        </select>
      </label>
      {identity && !inspecting && (
        <button type="button" onClick={() => setInspecting(true)}>
          Inspect selected request
        </button>
      )}
      {error && (
        <p role="status" className="quiet">
          {error}
        </p>
      )}
      {!identity && !error && (
        <p className="inspector-empty">
          Send a message to inspect its assembled context.
        </p>
      )}
      {value && (
        <>
          <div className="request-facts">
            <span>{value.request.model}</span>
            <span>{value.request.messages.length} messages</span>
            <span>{value.request.tools.length} tools</span>
            <span>
              Input ceiling: {value.input_budget_units.toLocaleString()} guard
              units
            </span>
            <span>
              Model capacity: {value.model_context_tokens.toLocaleString()}{" "}
              tokens
            </span>
            {value.input_units != null && (
              <span>
                Assembled input: {value.input_units.toLocaleString()} guard
                units
              </span>
            )}
            {typeof value.usage?.prompt_tokens === "number" && (
              <span>
                Measured prompt: {value.usage.prompt_tokens.toLocaleString()}{" "}
                tokens
              </span>
            )}
          </div>
          {value.request.messages.map((message, index) => (
            <details className="prompt-message" key={index} open>
              <summary>
                <span>{message.role}</span>
                <small>
                  {message.content.length.toLocaleString()} characters
                </small>
              </summary>
              <pre>{message.content}</pre>
              {message.tool_calls !== undefined && (
                <pre>{JSON.stringify(message.tool_calls, null, 2)}</pre>
              )}
              {message.tool_call_id !== undefined && (
                <small>Tool result: {String(message.tool_call_id)}</small>
              )}
            </details>
          ))}
          <details className="prompt-message">
            <summary>Tool definitions ({value.request.tools.length})</summary>
            <pre>{JSON.stringify(value.request.tools, null, 2)}</pre>
          </details>
          <details className="prompt-message">
            <summary>Complete request JSON</summary>
            <pre>{JSON.stringify(value.request, null, 2)}</pre>
          </details>
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard
                .writeText(JSON.stringify(value.request, null, 2))
                .then(() => setCopied(true))
                .catch(() => setError("Clipboard is unavailable."));
            }}
          >
            {copied ? "Copied" : "Copy request JSON"}
          </button>
        </>
      )}
    </div>
  );
}
