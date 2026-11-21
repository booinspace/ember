import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
    .max(600),
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
    .max(520),
  next_cursor: z.string().nullable(),
  truncated: z.boolean(),
  total_memories: z.number().int().nonnegative().nullable(),
});
type Graph = z.infer<typeof graphSchema>;
type Node = Graph["nodes"][number];
type Lens = { x: number; y: number; zoom: number };
const field = { w: 900, h: 560 };
const home: Lens = { x: field.w / 2, y: field.h / 2, zoom: 1 };
const initialCount = 1_000;
const nodePageSize = 100;
const formatCount = new Intl.NumberFormat();
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

function positions(graph: Graph) {
  const points = new Map(
    graph.nodes.map((node, index) => {
      const angle = index * 2.399963;
      const radius = Math.sqrt((index + 1) / graph.nodes.length);
      return [
        node.id,
        {
          node,
          x: home.x + Math.cos(angle) * (field.w / 2 - 35) * radius,
          y: home.y + Math.sin(angle) * (field.h / 2 - 30) * radius,
        },
      ];
    }),
  );
  const degrees = new Map<string, number>();
  for (const link of graph.links) {
    if (link.kind === "entity")
      degrees.set(link.target, (degrees.get(link.target) ?? 0) + 1);
  }
  const hubs = new Map<string, string>();
  for (const link of graph.links) {
    const degree = degrees.get(link.target) ?? Infinity;
    const previous = hubs.get(link.source);
    if (
      link.kind === "entity" &&
      degree > 1 &&
      degree <= 40 &&
      (!previous || degree < (degrees.get(previous) ?? Infinity))
    ) {
      hubs.set(link.source, link.target);
    }
  }
  for (const [source, target] of hubs) {
    const p = points.get(source);
    const hub = points.get(target);
    if (p && hub) {
      p.x = p.x * 0.58 + hub.x * 0.42;
      p.y = p.y * 0.58 + hub.y * 0.42;
    }
  }
  return points;
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
  const canvas = useRef<HTMLCanvasElement>(null);
  const [lens, setLens] = useState(home);
  const [hovered, setHovered] = useState<string | null>(null);
  const focus = hovered ?? selected;
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const neighbors = useMemo(() => {
    const ids = new Set<string>();
    if (focus) {
      for (const { source, target } of graph.links) {
        if (source === focus) ids.add(target);
        if (target === focus) ids.add(source);
      }
    }
    return ids;
  }, [graph, focus]);
  const scaleFor = (width: number, height: number) =>
    Math.min(width / field.w, height / field.h) * lens.zoom;
  const radius = points.size <= 100 ? 4 : points.size <= 1500 ? 2.3 : 1.5;

  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext("2d");
    if (!element || !context) return;
    function draw(
      element: HTMLCanvasElement,
      context: CanvasRenderingContext2D,
    ) {
      const { clientWidth: width, clientHeight: height } = element;
      if (!width || !height) return;
      const density = Math.min(2, window.devicePixelRatio || 1);
      const pixelWidth = Math.round(width * density);
      const pixelHeight = Math.round(height * density);
      if (element.width !== pixelWidth) element.width = pixelWidth;
      if (element.height !== pixelHeight) element.height = pixelHeight;
      context.resetTransform();
      context.clearRect(0, 0, pixelWidth, pixelHeight);
      const scale = Math.min(width / field.w, height / field.h) * lens.zoom;
      context.setTransform(
        scale * density,
        0,
        0,
        scale * density,
        (width / 2 - lens.x * scale) * density,
        (height / 2 - lens.y * scale) * density,
      );
      // Stroke each kind as a batch, keeping dense graphs inexpensive to repaint.
      for (const [kind, ink] of Object.entries(edgeColors)) {
        for (const highlighted of focus ? [false, true] : [false]) {
          context.beginPath();
          context.globalAlpha = highlighted ? 0.85 : focus ? 0.12 : 0.34;
          context.strokeStyle = ink;
          context.lineWidth = (highlighted ? 1.3 : 0.65) / scale;
          for (const link of graph.links) {
            if (
              link.kind !== kind ||
              (focus === link.source || focus === link.target) !== highlighted
            )
              continue;
            const from = points.get(link.source);
            const to = points.get(link.target);
            if (!from || !to) continue;
            context.moveTo(from.x, from.y);
            context.lineTo(to.x, to.y);
          }
          context.stroke();
        }
      }
      for (const [id, p] of points) {
        const active = id === focus;
        const size =
          radius + (p.node.category === "entity" ? 1.6 : 0) + (active ? 2 : 0);
        context.globalAlpha = focus && !active && !neighbors.has(id) ? 0.36 : 1;
        context.fillStyle = color(p.node);
        context.beginPath();
        context.arc(p.x, p.y, size, 0, 2 * Math.PI);
        context.fill();
      }
      const point = focus ? points.get(focus) : undefined;
      if (point) {
        const label =
          point.node.label.length > 42
            ? point.node.label.slice(0, 41) + "…"
            : point.node.label;
        context.globalAlpha = 1;
        context.font = `${13 / scale}px system-ui`;
        context.textBaseline = "middle";
        context.lineWidth = 4 / scale;
        context.strokeStyle = "#15101c";
        context.fillStyle = "#eee0f4";
        const x =
          point.x +
          (context.measureText(label).width * scale + 14 >
          width / 2 + (lens.x - point.x) * scale
            ? -context.measureText(label).width - 8 / scale
            : 8 / scale);
        context.strokeText(label, x, point.y);
        context.fillText(label, x, point.y);
      }
    }
    const observer = new ResizeObserver(() => draw(element, context));
    observer.observe(element);
    draw(element, context);
    return () => observer.disconnect();
  }, [graph, points, lens, focus, neighbors, radius]);

  function nodeAt(event: React.PointerEvent<HTMLCanvasElement>): string | null {
    const box = event.currentTarget.getBoundingClientRect();
    const scale = scaleFor(box.width, box.height);
    const x = lens.x + (event.clientX - box.left - box.width / 2) / scale;
    const y = lens.y + (event.clientY - box.top - box.height / 2) / scale;
    let distance = (Math.max(6, (radius + 2) * scale) / scale) ** 2;
    let nearest = null;
    for (const [id, point] of points) {
      const squared = (x - point.x) ** 2 + (y - point.y) ** 2;
      if (squared < distance) {
        nearest = id;
        distance = squared;
      }
    }
    return nearest;
  }

  return (
    <div className="constellation">
      <canvas
        ref={canvas}
        role="img"
        aria-label="Memory and entity connections"
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          drag.current = { x: event.clientX, y: event.clientY, moved: false };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start) {
            setHovered(nodeAt(event));
            return;
          }
          const dx = event.clientX - start.x;
          const dy = event.clientY - start.y;
          if (!start.moved && Math.hypot(dx, dy) < 3) return;
          setHovered(null);
          const scale = scaleFor(
            event.currentTarget.clientWidth,
            event.currentTarget.clientHeight,
          );
          setLens((old) => ({
            ...old,
            x: old.x - dx / scale,
            y: old.y - dy / scale,
          }));
          drag.current = { x: event.clientX, y: event.clientY, moved: true };
        }}
        onPointerUp={(event) => {
          if (drag.current && !drag.current.moved) {
            const id = nodeAt(event);
            if (id) select(id);
          }
          drag.current = null;
        }}
        onPointerLeave={() => setHovered(null)}
        onLostPointerCapture={() => {
          drag.current = null;
        }}
      />
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
            setLens((v) => ({ ...v, zoom: Math.min(16, v.zoom * 1.3) }))
          }
        >
          +
        </button>
      </div>
    </div>
  );
}

function ListPager({
  name,
  page,
  total,
  change,
}: {
  name: string;
  page: number;
  total: number;
  change: (page: number) => void;
}) {
  if (total <= nodePageSize) return null;
  return (
    <div className="graph-page-controls">
      <button type="button" disabled={!page} onClick={() => change(page - 1)}>
        Previous {name}
      </button>
      <button
        type="button"
        disabled={(page + 1) * nodePageSize >= total}
        onClick={() => change(page + 1)}
      >
        Next {name}
      </button>
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
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [revision, refresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [nodePage, setNodePage] = useState(0);
  const [linkPage, setLinkPage] = useState(0);
  const activeRead = useRef<AbortController | null>(null);
  const nodeById = useMemo(
    () => new Map(graph?.nodes.map((node) => [node.id, node])),
    [graph],
  );
  const drawn = useMemo(() => {
    if (!graph) return null;
    const displayed = new Set(graph.nodes.map(({ id }) => id));
    return {
      ...graph,
      links: graph.links.filter(
        ({ source, target }) => displayed.has(source) && displayed.has(target),
      ),
    };
  }, [graph]);
  const visibleLinks = useMemo(
    () =>
      drawn?.links.filter(
        ({ source, target }) => source === selected || target === selected,
      ) ?? [],
    [drawn, selected],
  );
  const selection = selected ? nodeById.get(selected) : undefined;
  function selectNode(id: string) {
    setSelected(id);
    setLinkPage(0);
  }
  const memoryCount =
    graph?.nodes.filter((node) => node.category === "memory").length ?? 0;

  useEffect(() => {
    void activate()
      .then(() => setAuthorized(true))
      .catch(() =>
        setError("Open the private activation link created by ember start."),
      );
  }, []);

  const load = useCallback(
    async (previous: Graph | null, amount: number) => {
      activeRead.current?.abort();
      const abort = new AbortController();
      activeRead.current = abort;
      setLoading(true);
      setError("");
      const nodes = new Map(previous?.nodes.map((node) => [node.id, node]));
      const links = new Map(
        previous?.links.map((link) => [
          link.source + link.target + link.kind,
          link,
        ]),
      );
      let cursor = previous?.next_cursor ?? null;
      let total = previous?.total_memories ?? null;
      let truncated = previous?.truncated ?? false;
      let loaded = 0;
      let published = 0;
      try {
        do {
          const batch: Graph = await control(
            "memory-graph",
            graphSchema,
            { text: term, kind, cursor, anchor },
            abort.signal,
          );
          if (abort.signal.aborted) return;
          if (cursor && batch.next_cursor === cursor)
            throw Error("Graph cursor did not advance.");
          cursor = batch.next_cursor;
          total = batch.total_memories ?? total;
          truncated ||= batch.truncated;
          for (const node of batch.nodes) {
            if (node.category === "memory" && !nodes.has(node.id)) loaded++;
            nodes.set(node.id, node);
          }
          for (const link of batch.links)
            links.set(link.source + link.target + link.kind, link);
          if (
            !cursor ||
            loaded >= amount ||
            loaded - published >= initialCount
          ) {
            setGraph({
              nodes: [...nodes.values()],
              links: [...links.values()],
              next_cursor: cursor,
              total_memories: total,
              truncated,
            });
            published = loaded;
          }
        } while (cursor && loaded < amount);
      } catch (reason: unknown) {
        if (abort.signal.aborted) return;
        setError(
          reason instanceof Error &&
            reason.message === "deep_deferred_for_foreground"
            ? "Ember is recalling memories for a reply. Refresh when it finishes."
            : "The memory graph is unavailable. Refresh to try again.",
        );
      } finally {
        if (!abort.signal.aborted) setLoading(false);
      }
    },
    [anchor, kind, term],
  );

  useEffect(() => {
    if (!authorized) return;
    setGraph(null);
    setSelected(anchor);
    setNodePage(0);
    setLinkPage(0);
    void load(null, initialCount);
    return () => activeRead.current?.abort();
  }, [authorized, anchor, load, revision]);

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
          setAnchor(null);
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
              setAnchor(null);
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
          <button type="button" onClick={() => setAnchor(null)}>
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
      {graph && drawn && (
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
                  graph={drawn}
                  selected={selected}
                  select={selectNode}
                />
              ) : (
                <div className="graph-empty">
                  {anchor
                    ? "This memory is unavailable."
                    : "No memories match this view."}
                </div>
              )}
              <footer className="graph-page-controls">
                <span role="status">
                  {formatCount.format(memoryCount)}
                  {graph.total_memories !== null &&
                    ` of ${formatCount.format(graph.total_memories)}`}{" "}
                  {(graph.total_memories ?? memoryCount) === 1
                    ? "memory"
                    : "memories"}{" "}
                  · {formatCount.format(drawn.links.length)}{" "}
                  {drawn.links.length === 1 ? "link" : "links"}
                </span>
                {loading && (
                  <button
                    type="button"
                    onClick={() => {
                      activeRead.current?.abort();
                      setLoading(false);
                    }}
                  >
                    Stop loading
                  </button>
                )}
                {graph.next_cursor && !loading && (
                  <>
                    <button
                      type="button"
                      onClick={() => void load(graph, initialCount)}
                    >
                      Load more
                    </button>
                    <button
                      type="button"
                      onClick={() => void load(graph, Infinity)}
                    >
                      Load all
                    </button>
                  </>
                )}
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
                          setAnchor(selection.id);
                        }}
                      >
                        Explore neighborhood
                      </button>
                    )}
                  <h3>Visible links</h3>
                  <ul className="graph-links">
                    {visibleLinks
                      .slice(
                        linkPage * nodePageSize,
                        (linkPage + 1) * nodePageSize,
                      )
                      .map((link) => {
                        const other = nodeById.get(
                          link.source === selection.id
                            ? link.target
                            : link.source,
                        );
                        return other ? (
                          <li key={link.source + link.target + link.kind}>
                            <button
                              type="button"
                              onClick={() => selectNode(other.id)}
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
                  <ListPager
                    name="links"
                    page={linkPage}
                    change={setLinkPage}
                    total={visibleLinks.length}
                  />
                </>
              ) : (
                <p className="quiet">
                  Select a node to see its memory type and linked records.
                </p>
              )}
              <details>
                <summary>Loaded nodes</summary>
                <ul className="graph-links">
                  {graph.nodes
                    .slice(
                      nodePage * nodePageSize,
                      (nodePage + 1) * nodePageSize,
                    )
                    .map((node) => (
                      <li key={node.id}>
                        <button
                          type="button"
                          aria-pressed={node.id === selected}
                          onClick={() => selectNode(node.id)}
                        >
                          <small style={{ color: color(node) }}>
                            {node.kind}
                          </small>
                          {node.label}
                        </button>
                      </li>
                    ))}
                </ul>
                <ListPager
                  name="nodes"
                  page={nodePage}
                  change={setNodePage}
                  total={graph.nodes.length}
                />
              </details>
            </aside>
          </div>
        </>
      )}
    </main>
  );
}
