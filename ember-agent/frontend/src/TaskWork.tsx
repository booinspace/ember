import { useEffect, useState } from "react";
import { z } from "zod";
import { control } from "./contracts";

const artifact = z.object({
  names: z.array(z.string().min(1).max(240)).max(256),
});
const calls = z
  .array(
    z.object({
      name: z.string().max(64),
      status: z.enum(["completed", "error"]),
    }),
  )
  .max(32);
const preview = z.object({
  name: z.string(),
  size_bytes: z.number().int().nonnegative(),
  text: z.string().max(32768).nullable(),
  truncated: z.boolean(),
});

function FileTree({
  names,
  select,
  prefix = "",
}: {
  names: string[];
  select: (name: string) => void;
  prefix?: string;
}) {
  const entries = new Set(
    names.map((name) => name.slice(prefix.length).split("/")[0]),
  );
  return (
    <ul>
      {[...entries].map((entry) => {
        const path = prefix + entry;
        const nested = names.filter((name) => name.startsWith(path + "/"));
        return (
          <li key={entry}>
            {nested.length ? (
              <details open>
                <summary>{entry}/</summary>
                <FileTree names={nested} prefix={path + "/"} select={select} />
              </details>
            ) : (
              <button onClick={() => select(path)}>{entry}</button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function CodePreview({ id, name }: { id: string; name: string }) {
  const [value, setValue] = useState<z.infer<typeof preview>>();
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void control(
      `tasks/${id}/file-preview?name=${encodeURIComponent(name)}`,
      preview,
      undefined,
      controller.signal,
    )
      .then((file) => {
        if (!controller.signal.aborted && file.name === name) setValue(file);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [id, name]);
  return (
    <section aria-label="File contents">
      <h4>{name}</h4>
      {value ? (
        <>
          <small>
            {value.size_bytes} bytes
            {value.truncated && " · Showing first 32 KiB"}
          </small>
          {value.text === null ? (
            <p>Binary or non-UTF-8 file. Download the ZIP to inspect it.</p>
          ) : (
            <pre tabIndex={0}>
              <code>{value.text}</code>
            </pre>
          )}
        </>
      ) : (
        <p role="status">
          {error ? "File preview unavailable." : "Loading file…"}
        </p>
      )}
    </section>
  );
}

export function TaskWork({
  id,
  data,
  build,
}: {
  id: string;
  data: Record<string, unknown>;
  build: boolean;
}) {
  const [selected, select] = useState("");
  const tools = calls.safeParse(data.tools).data;
  const files = build && artifact.safeParse(data.artifact).data;
  return (
    <>
      {tools && (
        <>
          <h3>Tool calls</h3>
          {tools.length ? (
            <ol>
              {tools.map((tool, index) => (
                <li key={index}>
                  <code>{tool.name}</code> · {tool.status}
                </li>
              ))}
            </ol>
          ) : (
            <p>No tool calls recorded.</p>
          )}
        </>
      )}
      {files && (
        <section aria-label="Generated files">
          <h3>Generated files ({files.names.length})</h3>
          <FileTree names={files.names} select={select} />
          {selected && <CodePreview key={selected} id={id} name={selected} />}
        </section>
      )}
    </>
  );
}
