import { useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";
import { activate, control } from "./contracts";
import "./MemoryGraph.css";

const graphSchema = z.object({
  nodes: z
    .array(
      z.object({
        id: z.string().uuid(),
        category: z.enum(["memory", "entity"]),
        kind: z.string(),
        label: z.string().max(600),
        modality: z.string().nullable(),
        recorded_at: z.string().nullable(),
      }),
    )
    .max(156),
  links: z
    .array(
      z.object({
        source: z.string().uuid(),
        target: z.string().uuid(),
        kind: z.enum([
          "entity",
          "supports",
          "conflicts",
          "redirects",
          "supersedes",
        ]),
      }),
    )
    .max(240),
  next_cursor: z.string().nullable(),
  truncated: z.boolean(),
});
type Graph = z.infer<typeof graphSchema>;
type Node = Graph["nodes"][number];
type Lens = { x: number; y: number; zoom: number };
const field = { w: 900, h: 560 };
const home: Lens = { x: field.w / 2, y: field.h / 2, zoom: 1 };
const colors: Record<string, string> = {
  fact: "#f5ad76",
  episode: "#72dacc",
  goal: "#c3a2fd",
  plan: "#f4d883",
  entity: "#99bffd",
};
const edgeColors: Record<Graph["links"][number]["kind"], string> = {
  entity: "#6b8bad",
  supports: "#6fc6aa",
  conflicts: "#f58696",
  redirects: "#c09cf3",
  supersedes: "#d1ae6b",
};
const color = (node: Node): string =>
  colors[node.category === "entity" ? "entity" : node.kind] ?? "#c7afb8";

// Start in a spiral, then pull linked nodes together. The result stays put while inspecting.
function positions(graph: Graph) {
  const points = graph.nodes.map((node, index) => {
    const angle = index * 2.4;
    const radius = Math.sqrt((index + 1) / graph.nodes.length);
    return {
      node,
      x: home.x + Math.cos(angle) * (field.w / 2 - 65) * radius,
      y: home.y + Math.sin(angle) * (field.h / 2 - 38) * radius,
    };
  });
  const byId = new Map(points.map((p) => [p.node.id, p]));
  for (let iteration = 0; iteration < 44; iteration++) {
    for (const a of points) {
      for (const b of points) {
        if (a === b) break;
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const distance = Math.max(10, Math.hypot(dx, dy));
        const force = Math.min(4, 1500 / (distance * distance));
        a.x += (dx / distance) * force;
        a.y += (dy / distance) * force;
        b.x -= (dx / distance) * force;
        b.y -= (dy / distance) * force;
      }
    }
    for (const link of graph.links) {
      const a = byId.get(link.source);
      const b = byId.get(link.target);
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const pull = (distance - 96) * 0.022;
      a.x += (dx / distance) * pull;
      a.y += (dy / distance) * pull;
      b.x -= (dx / distance) * pull;
      b.y -= (dy / distance) * pull;
    }
    for (const p of points) {
      p.x = Math.min(field.w - 32, Math.max(32, p.x));
      p.y = Math.min(field.h - 32, Math.max(32, p.y));
    }
  }
  return byId;
}

function Constellation({
  graph,
  selected,
  select,
}: {
  graph: Graph;
  selected: string | null;
  select: (id: string) => void;
}) {
  const points = useMemo(() => positions(graph), [graph]);
  const [lens, setLens] = useState(home);
  const [hovered, setHovered] = useState<string | null>(null);
  const focus = hovered ?? selected;
  const drag = useRef<{ x: number; y: number } | null>(null);
  const neighbors = useMemo(
    () =>
      new Set(
        graph.links.flatMap(({ source, target }) =>
          source === focus ? [target] : target === focus ? [source] : [],
        ),
      ),
    [graph, focus],
  );
  const width = field.w / lens.zoom;
  const height = field.h / lens.zoom;

  return (
    <div className="constellation">
      <svg
        aria-label="Memory and entity connections"
        viewBox={`${lens.x - width / 2} ${lens.y - height / 2} ${width} ${height}`}
        onPointerDown={(event) => {
          if (event.target !== event.currentTarget || event.button !== 0)
            return;
          drag.current = { x: event.clientX, y: event.clientY };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start) return;
          const scale = Math.max(
            width / event.currentTarget.clientWidth,
            height / event.currentTarget.clientHeight,
          );
          setLens((old) => ({
            ...old,
            x: old.x - (event.clientX - start.x) * scale,
            y: old.y - (event.clientY - start.y) * scale,
          }));
          drag.current = { x: event.clientX, y: event.clientY };
        }}
        onLostPointerCapture={() => {
          drag.current = null;
        }}
      >
        {graph.links.map((link, index) => {
          const source = points.get(link.source);
          const target = points.get(link.target);
          if (!source || !target) return null;
          const highlighted = focus === link.source || focus === link.target;
          return (
            <line
              key={index}
              x1={source.x}
              y1={source.y}
              x2={target.x}
              y2={target.y}
              stroke={edgeColors[link.kind]}
              strokeWidth={highlighted ? 2 : 1}
              opacity={highlighted ? 0.9 : focus ? 0.19 : 0.52}
            />
          );
        })}
        {[...points.values()].map(({ node, x, y }, index) => {
          const active = node.id === focus;
          const near = neighbors.has(node.id);
          const radius = node.category === "entity" ? 8 : 5.5;
          return (
            <g
              key={node.id}
              className="constellation-node"
              transform={`translate(${x} ${y})`}
              onClick={() => select(node.id)}
              onPointerEnter={() => setHovered(node.id)}
              onPointerLeave={() => setHovered(null)}
              data-selected={node.id === selected}
              opacity={focus && !active && !near ? 0.58 : 1}
            >
              <title>{`${node.kind}: ${node.label}`}</title>
              <circle
                r={radius + (active ? 8 : 5)}
                fill={color(node)}
                opacity={active ? 0.27 : 0.13}
              />
              <circle r={radius + (active ? 2 : 0)} fill={color(node)} />
              {(active || near || (!focus && index < 8)) && (
                <text x={radius + 7} y={4}>
                  {node.label.length > 24
                    ? node.label.slice(0, 23) + "…"
                    : node.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="constellation-tools" aria-label="Graph zoom">
        <button
          type="button"
          aria-label="Zoom out"
          onClick={() =>
            setLens((v) => ({ ...v, zoom: Math.max(0.7, v.zoom / 1.3) }))
          }
        >
          −
        </button>
        <button type="button" onClick={() => setLens(home)}>
          Reset view
        </button>
        <button
          type="button"
          aria-label="Zoom in"
          onClick={() =>
            setLens((v) => ({ ...v, zoom: Math.min(4, v.zoom * 1.3) }))
          }
        >
          +
        </button>
      </div>
    </div>
  );
}

export function MemoryGraphPage() {
  const [authorized, setAuthorized] = useState(false);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [input, setInput] = useState("");
  const [term, setTerm] = useState("");
  const [kind, setKind] = useState("");
  const [anchor, setAnchor] = useState<string | null>(null);
  const [cursors, setCursors] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [revision, refresh] = useState(0);
  const cursor = cursors.at(-1);
  const selection = graph?.nodes.find((node) => node.id === selected);
  const memoryCount =
    graph?.nodes.filter((node) => node.category === "memory").length ?? 0;

  useEffect(() => {
    void activate()
      .then(() => setAuthorized(true))
      .catch(() =>
        setError("Open the private activation link created by ember start."),
      );
  }, []);

  useEffect(() => {
    if (!authorized) return;
    const abort = new AbortController();
    setGraph(null);
    setError("");
    setSelected(anchor);
    void control(
      "memory-graph",
      graphSchema,
      { text: term, kind, cursor: cursor ?? null, anchor },
      abort.signal,
    )
      .then((result) => {
        if (!abort.signal.aborted) setGraph(result);
      })
      .catch((reason: unknown) => {
        if (abort.signal.aborted) return;
        setError(
          reason instanceof Error &&
            reason.message === "deep_deferred_for_foreground"
            ? "Ember is recalling memories for a reply. Refresh when it finishes."
            : "The memory graph is unavailable. Refresh to try again.",
        );
      });
    return () => abort.abort();
  }, [authorized, cursor, anchor, kind, term, revision]);

  function resetFocus() {
    setAnchor(null);
    setCursors([]);
    setSelected(null);
  }

  return (
    <main className="dashboard-shell graph-page">
      <header className="app-header">
        <div className="mark" aria-hidden="true">
          e
        </div>
        <div>
          <h1>Memory graph</h1>
        </div>
        <a className="graph-back" href="/">
          Back to workspace
        </a>
      </header>
      <form
        className="graph-filters"
        onSubmit={(event) => {
          event.preventDefault();
          resetFocus();
          setTerm(input.trim());
        }}
      >
        <label>
          Search memories
          <input
            value={input}
            maxLength={128}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Search extracted values"
          />
        </label>
        <label>
          Memory type
          <select
            value={kind}
            onChange={(event) => {
              resetFocus();
              setKind(event.target.value);
            }}
          >
            <option value="">All types</option>
            <option value="fact">Facts</option>
            <option value="episode">Episodes</option>
            <option value="goal">Goals</option>
            <option value="plan">Plans</option>
          </select>
        </label>
        <button type="submit">Search</button>
        <button type="button" onClick={() => refresh((value) => value + 1)}>
          Refresh
        </button>
        {anchor && (
          <button type="button" onClick={resetFocus}>
            Exit neighborhood
          </button>
        )}
      </form>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!graph && !error && <p role="status">Loading the memory graph…</p>}
      {graph && (
        <>
          <div className="graph-workspace">
            <section className="graph-panel" aria-label="Memory constellation">
              <div className="graph-legend" aria-label="Node colors">
                {Object.entries(colors).map(([label, value]) => (
                  <span key={label}>
                    <i style={{ background: value }} />
                    {label}
                  </span>
                ))}
              </div>
              {graph.nodes.length ? (
                <Constellation
                  graph={graph}
                  selected={selected}
                  select={setSelected}
                />
              ) : (
                <div className="graph-empty">
                  {anchor
                    ? "This memory is unavailable."
                    : "No memories match this view."}
                </div>
              )}
              <footer className="graph-page-controls">
                <span>
                  {memoryCount} {memoryCount === 1 ? "memory" : "memories"} ·{" "}
                  {graph.links.length}{" "}
                  {graph.links.length === 1 ? "link" : "links"}
                </span>
                <button
                  type="button"
                  disabled={!cursor}
                  onClick={() => setCursors((pages) => pages.slice(0, -1))}
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={!graph.next_cursor}
                  onClick={() => {
                    if (graph.next_cursor)
                      setCursors([...cursors, graph.next_cursor]);
                  }}
                >
                  Next
                </button>
              </footer>
              {graph.truncated && (
                <p className="quiet graph-limit">
                  This view shows a bounded set of links. Narrow the search to
                  explore further.
                </p>
              )}
            </section>
            <aside className="graph-inspector" aria-label="Memory details">
              <h2>Inspect</h2>
              {selection ? (
                <>
                  <p className="graph-type" style={{ color: color(selection) }}>
                    {selection.kind}
                  </p>
                  <p className="graph-label">{selection.label}</p>
                  {selection.modality && (
                    <p className="quiet">
                      {selection.modality.toLowerCase().replaceAll("_", " ")}
                    </p>
                  )}
                  {selection.recorded_at && (
                    <p className="quiet">
                      Recorded{" "}
                      <time dateTime={selection.recorded_at}>
                        {new Date(selection.recorded_at).toLocaleDateString()}
                      </time>
                    </p>
                  )}
                  {selection.category === "memory" &&
                    selection.id !== anchor && (
                      <button
                        type="button"
                        onClick={() => {
                          setCursors([]);
                          setAnchor(selection.id);
                        }}
                      >
                        Explore neighborhood
                      </button>
                    )}
                  <h3>Visible links</h3>
                  <ul className="graph-links">
                    {graph.links
                      .filter(
                        ({ source, target }) =>
                          source === selection.id || target === selection.id,
                      )
                      .map((link, index) => {
                        const other = graph.nodes.find(
                          ({ id }) =>
                            id ===
                            (link.source === selection.id
                              ? link.target
                              : link.source),
                        );
                        return other ? (
                          <li key={index}>
                            <button
                              type="button"
                              onClick={() => setSelected(other.id)}
                            >
                              <small style={{ color: edgeColors[link.kind] }}>
                                {link.kind}
                              </small>
                              {other.label}
                            </button>
                          </li>
                        ) : null;
                      })}
                  </ul>
                </>
              ) : (
                <p className="quiet">
                  Select a node to see its memory type and linked records.
                </p>
              )}
              <details>
                <summary>Nodes on this page</summary>
                <ul className="graph-links">
                  {graph.nodes.map((node) => (
                    <li key={node.id}>
                      <button
                        type="button"
                        aria-pressed={node.id === selected}
                        onClick={() => setSelected(node.id)}
                      >
                        <small style={{ color: color(node) }}>
                          {node.kind}
                        </small>
                        {node.label}
                      </button>
                    </li>
                  ))}
                </ul>
              </details>
            </aside>
          </div>
        </>
      )}
    </main>
  );
}
