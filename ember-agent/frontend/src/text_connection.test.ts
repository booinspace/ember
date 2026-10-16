import { afterEach, expect, it, vi } from "vitest";
import { runtimeState } from "./contracts";
import { TextConnection } from "./text_connection";

const identity = "d137c99a-1511-4a48-8ce4-9013290d899d";
const state = runtimeState.parse({
  binding: {
    character_id: identity,
    instance_id: identity,
    room: "synthetic",
    endpoint: "ws://127.0.0.1:7880",
  },
  context_id: identity,
  recall: {
    local_enabled: true,
    import_mode: "all",
    included_libraries: [],
    excluded_libraries: [],
    revision: 0,
  },
  capture: { enabled: true, revision: 0 },
  deep: "available",
  transport: "http",
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("displays a matching buffered reply before acknowledging its delivery", async () => {
  const events: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string) as { request_id?: string };
      if (path.endsWith("connect") && !path.endsWith("disconnect"))
        return Response.json({ session_id: identity });
      if (path.endsWith("turn"))
        return Response.json({
          request_id: body.request_id,
          text: "Synthetic response",
          retained_memories: 1,
        });
      if (path.endsWith("acknowledge")) {
        events.push("acknowledged");
        return Response.json({ delivered: true });
      }
      events.push("disconnected");
      return Response.json({ connected: false });
    }),
  );
  const client = new TextConnection(
    (text) => events.push(text),
    vi.fn(),
    vi.fn(),
  );
  await client.connect(state);
  expect(await client.ask("Synthetic question")).toBe(1);
  expect(events).toEqual(["Synthetic response", "acknowledged"]);
  await client.close();
  expect(events.at(-1)).toBe("disconnected");
});

it("releases a lease that arrives after the browser was closed", async () => {
  let resolve!: (response: Response) => void;
  const disconnect = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) => {
      if (path.endsWith("disconnect")) {
        disconnect();
        return Promise.resolve(Response.json({ connected: false }));
      }
      return new Promise<Response>((ready) => {
        resolve = ready;
      });
    }),
  );
  const client = new TextConnection(vi.fn(), vi.fn(), vi.fn());
  const connecting = client.connect(state);
  await client.close();
  resolve(Response.json({ session_id: identity }));
  await expect(connecting).rejects.toThrow("Connection closed");
  expect(disconnect).toHaveBeenCalledOnce();
});

it("expires a failed heartbeat and stops its owned timer", async () => {
  vi.useFakeTimers();
  const disconnected = vi.fn();
  const failure = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.endsWith("heartbeat"))
        return Response.json(
          { error: "text_session_expired" },
          { status: 409 },
        );
      if (path.endsWith("disconnect"))
        return Response.json({ connected: false });
      return Response.json({ session_id: identity });
    }),
  );
  const client = new TextConnection(vi.fn(), failure, disconnected);
  await client.connect(state);
  await vi.advanceTimersByTimeAsync(15000);
  expect(disconnected).toHaveBeenCalledOnce();
  expect(failure).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(45000);
  expect(disconnected).toHaveBeenCalledOnce();
  await client.close();
});
