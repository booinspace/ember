import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { control } from "./contracts";

export const skillSummary = z.object({
  id: z.string().max(192),
  name: z.string().max(128),
  description: z.string().max(512),
  version: z.string(),
  source: z.string(),
  content_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  enabled: z.boolean(),
  requirements: z.array(z.string()).max(64),
  unavailable: z.array(z.string()).max(32),
  diagnostics: z.array(z.string()).max(32),
});
export const skillCatalog = z.object({
  provider: z.string(),
  revision: z.string(),
  skills: z.array(skillSummary).max(256),
  unavailable: z.string().nullable(),
});
export const skillDocument = z.object({
  skill: skillSummary,
  markdown: z.string().max(65536),
  metadata: z.record(z.string(), z.unknown()),
  extensions: z.record(z.string(), z.unknown()),
  resources: z
    .array(
      z.object({
        path: z.string(),
        sha256: z.string(),
        size_bytes: z.number().int(),
      }),
    )
    .max(128),
});
const bundle = z.object({
  name: z.string(),
  files: z.record(z.string(), z.string()),
});
type Summary = z.infer<typeof skillSummary>;

export function Skills({
  enabled,
  useSkill,
}: {
  enabled: boolean;
  useSkill: (name: string) => void;
}) {
  const [catalog, setCatalog] = useState<z.infer<typeof skillCatalog> | null>(
    null,
  );
  const [document, setDocument] = useState<z.infer<
    typeof skillDocument
  > | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    void control("skills", skillCatalog, undefined, abort.signal)
      .then(setCatalog)
      .catch(() => setError("Skill catalog unavailable."));
    return () => abort.abort();
  }, [enabled]);
  async function inspect(item: Summary) {
    const current = ++generation.current;
    setDocument(null);
    setBusy(true);
    setError("");
    try {
      const value = await control(
        "skills/document?identity=" + encodeURIComponent(item.id),
        skillDocument,
      );
      if (generation.current === current) setDocument(value);
    } catch {
      setError(
        "The provider could not inspect this package. Refresh its catalog.",
      );
    } finally {
      if (generation.current === current) setBusy(false);
    }
  }
  async function select() {
    if (!document || !catalog) return;
    setBusy(true);
    setError("");
    try {
      const value = await control("skills/select", skillCatalog, {
        id: document.skill.id,
        content_sha256: document.skill.content_sha256,
        enabled: !document.skill.enabled,
        revision: catalog.revision,
      });
      setCatalog(value);
      setDocument(null);
      generation.current++;
    } catch {
      setError(
        "Selection failed or changed. Refresh and inspect the exact package before retrying.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    setBusy(true);
    setError("");
    setDocument(null);
    generation.current++;
    try {
      setCatalog(await control("skills/refresh", skillCatalog, {}));
    } catch {
      setError("The selected harness is unavailable.");
    } finally {
      setBusy(false);
    }
  }
  async function importFile(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      if (file.size > 196608) throw Error("too_large");
      const value = bundle.parse(JSON.parse(await file.text()));
      setCatalog(await control("skills/import", skillCatalog, value));
      setDocument(null);
      generation.current++;
    } catch {
      setError(
        "Import failed. Use an exported portable skill JSON package; existing packages are preserved.",
      );
    } finally {
      setBusy(false);
    }
  }
  const items = (catalog?.skills ?? []).filter(
    (item) =>
      (filter !== "enabled" || item.enabled) &&
      (filter !== "unavailable" || item.unavailable.length > 0) &&
      (item.id + " " + item.name + " " + item.description)
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <details
      className="dashboard-panel skills-panel"
      id="skills"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className="panel-heading">
        <div>
          <span className="eyebrow">SHARED CAPABILITY LIBRARY</span>
          <h2>Skills</h2>
        </div>
        <span>
          {catalog?.skills.filter((s) => s.enabled).length ?? 0} enabled
        </span>
      </summary>
      {open && (
        <div className="inspector-body">
          <p className="quiet">
            {catalog?.provider ?? "Harness"} owns skill loading. Required tools
            use Ember’s existing grants and approvals.
          </p>
          <div className="skill-actions">
            <button disabled={busy || !enabled} onClick={() => void refresh()}>
              Refresh skills
            </button>
            <label className="file-button">
              Import package
              <input
                type="file"
                accept="application/json,.json"
                disabled={busy}
                onChange={(e) => {
                  void importFile(e.currentTarget.files?.[0]);
                  e.currentTarget.value = "";
                }}
              />
            </label>
          </div>
          {catalog?.unavailable && (
            <p role="status">{catalog.unavailable.replaceAll("_", " ")}</p>
          )}
          <label>
            Find skills
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search names, providers or descriptions"
            />
          </label>
          <label>
            Show
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">All available packages</option>
              <option value="enabled">Enabled</option>
              <option value="unavailable">Requirements unavailable</option>
            </select>
          </label>
          {error && <p role="alert">{error}</p>}
          <div className="skill-list">
            {items.slice(0, 40).map((item) => (
              <button
                key={item.id}
                className="skill-row"
                disabled={busy}
                onClick={() => void inspect(item)}
              >
                <strong>{item.name}</strong>
                <small>
                  {item.id} ·{" "}
                  {item.enabled
                    ? "Enabled"
                    : item.unavailable.length
                      ? "Unavailable"
                      : "Available"}
                </small>
                <span>{item.description}</span>
              </button>
            ))}
          </div>
          <p className="quiet">
            {items.length} matching packages
            {items.length > 40 ? " · narrow the search to see more" : ""}. Only
            selected complete instructions enter a task.
          </p>
          {document && (
            <section className="skill-document" aria-label="Selected skill">
              <div className="panel-heading">
                <h3>{document.skill.name}</h3>
                <button
                  onClick={() => {
                    setDocument(null);
                    generation.current++;
                  }}
                >
                  Close skill
                </button>
              </div>
              <p className="quiet">
                {document.skill.id} · {document.skill.version}
              </p>
              <code>{document.skill.content_sha256}</code>
              <p>
                Requirements:{" "}
                {document.skill.requirements.join(", ") ||
                  "No declared tool requirements"}
              </p>
              {document.skill.unavailable.map((value) => (
                <p role="status" key={value}>
                  {value.replaceAll("_", " ")}
                </p>
              ))}
              {document.skill.diagnostics.map((value) => (
                <p className="quiet" key={value}>
                  {value.replaceAll("_", " ")}
                </p>
              ))}
              <div className="skill-actions">
                <button
                  disabled={
                    busy ||
                    (!document.skill.enabled &&
                      document.skill.unavailable.length > 0)
                  }
                  onClick={() => void select()}
                >
                  {document.skill.enabled ? "Disable skill" : "Enable skill"}
                </button>
                <button
                  disabled={
                    busy ||
                    !document.skill.enabled ||
                    document.skill.unavailable.length > 0
                  }
                  onClick={() => useSkill(document.skill.id)}
                >
                  Use in conversation
                </button>
                <a
                  download="ember-skill.json"
                  href={
                    "/api/skills/export?identity=" +
                    encodeURIComponent(document.skill.id)
                  }
                >
                  Export package
                </a>
              </div>
              <details open>
                <summary>SKILL.md</summary>
                <pre>{document.markdown}</pre>
              </details>
              <details>
                <summary>Resources ({document.resources.length})</summary>
                <ul>
                  {document.resources.map((file) => (
                    <li key={file.path}>
                      {file.path} · {file.size_bytes} bytes
                    </li>
                  ))}
                </ul>
                <p className="quiet">
                  Resources load on demand. Scripts and installers are not
                  executed by viewing a skill.
                </p>
              </details>
              <details>
                <summary>Original provider metadata</summary>
                <pre>{JSON.stringify(document.metadata, null, 2)}</pre>
              </details>
            </section>
          )}
        </div>
      )}
    </details>
  );
}
