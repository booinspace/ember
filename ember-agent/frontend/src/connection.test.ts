import { afterEach, describe, expect, it, vi } from "vitest";
import { runtimeState } from "./contracts";
import { CharacterConnection } from "./connection";

const sdk = vi.hoisted(() => {
  type Sender = { identity: string };
  type Reader = {
    info: { id: string; attributes?: Record<string, string> };
    withAbortSignal: (signal: AbortSignal) => AsyncIterable<string>;
  };
  class Room {
    callbacks = new Map<string, (...args: unknown[]) => void>();
    handler: ((reader: Reader, sender: Sender) => void) | null = null;
    registerTextStreamHandler(_topic: string, handler: typeof this.handler) {
      this.handler = handler;
    }
    on(event: string, callback: (...args: unknown[]) => void) {
      this.callbacks.set(event, callback);
    }
    connect = vi.fn(async () => undefined);
    disconnect = vi.fn(async () => undefined);
    startAudio = vi.fn(async () => undefined);
    localParticipant = {
      audioTrackPublications: new Map<string, object>(),
      performRpc: vi.fn(),
      setMicrophoneEnabled: vi.fn(async () => undefined),
    };
  }
  return { Room };
});
vi.mock("livekit-client", () => ({
  Room: sdk.Room,
  RoomEvent: {
    Disconnected: "disconnected",
    TrackSubscribed: "subscribed",
    TrackUnsubscribed: "unsubscribed",
  },
  Track: { Kind: { Audio: "audio" } },
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function roomOf(client: CharacterConnection): InstanceType<typeof sdk.Room> {
  return client.room as unknown as InstanceType<typeof sdk.Room>;
}

describe("connection ownership", () => {
  it("aborts and drains an incomplete stream without waiting for its publisher", async () => {
    const text = vi.fn();
    const failure = vi.fn();
    const client = new CharacterConnection(text, failure);
    let drained = false;
    const reader = {
      info: { id: "answer" },
      withAbortSignal(signal: AbortSignal) {
        return (async function* () {
          try {
            yield "heard";
            await new Promise<void>((_resolve, reject) => {
              if (signal.aborted) reject(signal.reason);
              else
                signal.addEventListener("abort", () => reject(signal.reason), {
                  once: true,
                });
            });
          } finally {
            drained = true;
          }
        })();
      },
    };
    roomOf(client).handler?.(reader, { identity: "ember-agent" });
    await Promise.resolve();
    await client.close();
    expect(drained).toBe(true);
    expect(text).toHaveBeenCalledWith("heard", "answer", "assistant");
    expect(failure).not.toHaveBeenCalled();
    await client.close();
    expect(roomOf(client).disconnect).toHaveBeenCalledTimes(1);
  });

  it("removes unsubscribed audio and reports unexpected disconnection", async () => {
    vi.stubGlobal("document", { body: { appendChild: vi.fn() } });
    const disconnected = vi.fn();
    const client = new CharacterConnection(vi.fn(), vi.fn(), disconnected);
    const element = { remove: vi.fn() };
    const track = {
      kind: "audio",
      attach: () => element,
      detach: () => [element],
    };
    const room = roomOf(client);
    room.callbacks.get("subscribed")?.(track, {}, { identity: "ember-agent" });
    room.callbacks.get("unsubscribed")?.(track);
    expect(element.remove).toHaveBeenCalledTimes(1);
    room.callbacks.get("disconnected")?.();
    expect(disconnected).toHaveBeenCalledTimes(1);
    await client.close();
    expect(element.remove).toHaveBeenCalledTimes(1);
  });

  it("cannot connect after teardown while room authorization is pending", async () => {
    let release: (() => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            release = () =>
              resolve(
                new Response(
                  JSON.stringify({
                    endpoint: "ws://127.0.0.1:7880",
                    token: "synthetic",
                  }),
                ),
              );
          }),
      ),
    );
    const client = new CharacterConnection(vi.fn(), vi.fn());
    const state = runtimeState.parse({
      binding: {
        character_id: "11111111-1111-4111-8111-111111111111",
        instance_id: "22222222-2222-4222-8222-222222222222",
        room: "synthetic",
        endpoint: "ws://127.0.0.1:7880",
      },
      context_id: "33333333-3333-4333-8333-333333333333",
      recall: {
        local_enabled: true,
        import_mode: "all",
        included_libraries: [],
        excluded_libraries: [],
        revision: 0,
      },
      capture: { enabled: false, revision: 0 },
      deep: "enabled",
    });
    const attempt = client.connect(state);
    const outcome = expect(attempt).rejects.toThrow("Connection closed.");
    await client.close();
    release?.();
    await outcome;
    expect(roomOf(client).connect).not.toHaveBeenCalled();
  });
});

function configuredState(voice = false) {
  return runtimeState.parse({
    binding: {
      character_id: "11111111-1111-4111-8111-111111111111",
      instance_id: "22222222-2222-4222-8222-222222222222",
      room: "synthetic",
      endpoint: "ws://127.0.0.1:7880",
    },
    context_id: "33333333-3333-4333-8333-333333333333",
    recall: {
      local_enabled: true,
      import_mode: "all",
      included_libraries: [],
      excluded_libraries: [],
      revision: 0,
    },
    capture: { enabled: false, revision: 0 },
    deep: "enabled",
    voice,
  });
}

function authorizeRoom() {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            endpoint: "ws://127.0.0.1:7880",
            token: "synthetic",
          }),
        ),
    ),
  );
}

describe("browser room readiness", () => {
  it("connects text without requiring browser audio playback", async () => {
    authorizeRoom();
    const state = configuredState();
    const client = new CharacterConnection(vi.fn(), vi.fn());
    const room = roomOf(client);
    room.localParticipant.performRpc.mockResolvedValue(
      JSON.stringify(state.binding),
    );
    room.startAudio.mockRejectedValue(new Error("Playback unavailable"));
    await client.connect(state);
    expect(room.startAudio).not.toHaveBeenCalled();
    expect(room.localParticipant.performRpc).toHaveBeenCalledWith({
      destinationIdentity: "ember-agent",
      method: "ember.ready",
      payload: "",
    });
    await client.close();
  });

  it("reports room join failure without exposing SDK credentials", async () => {
    authorizeRoom();
    const client = new CharacterConnection(vi.fn(), vi.fn());
    roomOf(client).connect.mockRejectedValue(
      new Error("PRIVATE_SDK_TOKEN expired room"),
    );
    await expect(client.connect(configuredState())).rejects.toThrow(
      "LiveKit room is unavailable. Restart Ember and reopen its activation link.",
    );
    await client.close();
  });

  it("rejects a worker bound to a different character", async () => {
    authorizeRoom();
    const state = configuredState();
    const client = new CharacterConnection(vi.fn(), vi.fn());
    roomOf(client).localParticipant.performRpc.mockResolvedValue(
      JSON.stringify({ ...state.binding, character_id: state.context_id }),
    );
    await expect(client.connect(state)).rejects.toThrow(
      "The Ember worker is unavailable in this room.",
    );
    await client.close();
  });

  it("retains text connectivity when audio is blocked and retries on microphone gesture", async () => {
    authorizeRoom();
    const state = configuredState(true);
    const failure = vi.fn();
    const client = new CharacterConnection(vi.fn(), failure);
    const room = roomOf(client);
    room.localParticipant.performRpc.mockResolvedValue(
      JSON.stringify(state.binding),
    );
    room.startAudio.mockRejectedValueOnce(new Error("Browser playback denied"));
    await client.connect(state);
    expect(failure).toHaveBeenCalledWith(
      expect.stringContaining("Text chat is ready"),
    );
    await client.microphone(true);
    expect(room.startAudio).toHaveBeenCalledTimes(2);
    expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(
      true,
    );
    await client.close();
  });
});

describe("speech transcript ownership", () => {
  it("labels the agent-published microphone transcript as user and ignores interim snapshots", async () => {
    const text = vi.fn();
    const client = new CharacterConnection(text, vi.fn());
    const room = roomOf(client);
    room.localParticipant.audioTrackPublications.set("TR_microphone", {});
    const stream = (final: boolean) => ({
      info: {
        id: "transcript",
        attributes: {
          "lk.transcribed_track_id": "TR_microphone",
          "lk.segment_id": "utterance",
          "lk.transcription_final": String(final),
        },
      },
      async *withAbortSignal() {
        yield "What is my favorite color?";
      },
    });
    room.handler?.(stream(false), { identity: "ember-agent" });
    room.handler?.(stream(true), { identity: "ember-agent" });
    await Promise.resolve();
    await client.close();
    expect(text).toHaveBeenCalledTimes(1);
    expect(text).toHaveBeenCalledWith(
      "What is my favorite color?",
      "utterance",
      "user",
    );
  });
});
