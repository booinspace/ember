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
    handlers = new Map<string, NonNullable<typeof this.handler>>();
    registerTextStreamHandler(
      topic: string,
      handler: NonNullable<typeof this.handler>,
    ) {
      this.handlers.set(topic, handler);
      if (topic === "lk.transcription") this.handler = handler;
    }
    on(event: string, callback: (...args: unknown[]) => void) {
      this.callbacks.set(event, callback);
    }
    connect = vi.fn(async () => undefined);
    disconnect = vi.fn(async () => undefined);
    startAudio = vi.fn(async () => undefined);
    localParticipant = {
      audioTrackPublications: new Map<string, object>(),
      rpcHandlers: new Map<
        string,
        (data: { callerIdentity: string; payload: string }) => Promise<string>
      >(),
      registerRpcMethod(
        name: string,
        handler: (data: {
          callerIdentity: string;
          payload: string;
        }) => Promise<string>,
      ) {
        this.rpcHandlers.set(name, handler);
      },
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
  it("shows immediate reply text once and rejects synchronized copies and other senders", async () => {
    const text = vi.fn();
    const client = new CharacterConnection(text, vi.fn());
    const reader = {
      info: { id: "answer" },
      withAbortSignal: async function* () {
        yield "Read ahead.";
      },
    };
    const room = roomOf(client);
    room.handlers.get("lk.transcription")?.(reader, {
      identity: "ember-agent",
    });
    room.handlers.get("ember.reply")?.(reader, { identity: "ember-user" });
    room.handlers.get("ember.reply")?.(reader, { identity: "ember-agent" });
    await Promise.resolve();
    await client.close();
    expect(text).toHaveBeenCalledExactlyOnceWith(
      "Read ahead.",
      "answer",
      "assistant",
    );
  });

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
    roomOf(client).handlers.get("ember.reply")?.(reader, {
      identity: "ember-agent",
    });
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
      attachedElements: [],
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

describe("targeted Stop speaking", () => {
  it("stops local audio before the server drains, keeps mic/text live and resumes only a fresh reply", async () => {
    authorizeRoom();
    const state = configuredState(true);
    const text = vi.fn();
    const changed = vi.fn();
    const client = new CharacterConnection(text, vi.fn(), vi.fn(), changed);
    const room = roomOf(client);
    room.localParticipant.performRpc.mockResolvedValue(
      JSON.stringify(state.binding),
    );
    await client.connect(state);
    await client.microphone(true);
    const elements: {
      pause: ReturnType<typeof vi.fn>;
      remove: ReturnType<typeof vi.fn>;
      srcObject: object | null;
    }[] = [];
    const track = {
      kind: "audio",
      attachedElements: [] as typeof elements,
      attach() {
        const element = { pause: vi.fn(), remove: vi.fn(), srcObject: {} };
        elements.push(element);
        this.attachedElements.push(element);
        return element;
      },
      detach() {
        const old = this.attachedElements;
        this.attachedElements = [];
        return old;
      },
    };
    vi.stubGlobal("document", { body: { appendChild: vi.fn() } });
    room.callbacks.get("subscribed")?.(track, {}, { identity: "ember-agent" });
    const identity = "44444444-4444-4444-8444-444444444444";
    const target = {
      instance_id: state.binding.instance_id,
      character_id: state.binding.character_id,
      context_id: state.context_id,
      request_id: identity,
    };
    const started = room.localParticipant.rpcHandlers.get(
      "ember.reply_started",
    )!;
    await started({
      callerIdentity: "ember-agent",
      payload: JSON.stringify(target),
    });
    let continueText!: () => void;
    const suffix = new Promise<void>((resolve) => {
      continueText = resolve;
    });
    room.handlers.get("ember.reply")?.(
      {
        info: { id: identity },
        withAbortSignal: async function* () {
          yield "Read-ahead text stays visible.";
          await suffix;
          yield " The remaining text stays readable.";
        },
      },
      { identity: "ember-agent" },
    );
    await Promise.resolve();
    let release!: (reply: string) => void;
    room.localParticipant.performRpc.mockImplementation(
      () =>
        new Promise<string>((resolve) => {
          release = resolve;
        }),
    );
    const stop = client.stopSpeaking();
    const duplicate = client.stopSpeaking();
    continueText();
    await Promise.resolve();
    await Promise.resolve();
    expect(stop).toBe(duplicate);
    expect(elements[0]!.pause).toHaveBeenCalledOnce();
    expect(elements[0]!.srcObject).toBeNull();
    expect(elements[0]!.remove).toHaveBeenCalledOnce();
    expect(track.attachedElements).toHaveLength(0);
    expect(room.disconnect).not.toHaveBeenCalled();
    expect(
      room.localParticipant.setMicrophoneEnabled,
    ).toHaveBeenCalledExactlyOnceWith(true);
    expect(room.localParticipant.performRpc).toHaveBeenLastCalledWith({
      destinationIdentity: "ember-agent",
      method: "ember.stop_speaking",
      payload: JSON.stringify(target),
      responseTimeout: 190000,
    });
    // A delayed duplicate start cannot reattach the stopped stream.
    await started({
      callerIdentity: "ember-agent",
      payload: JSON.stringify(target),
    });
    expect(elements).toHaveLength(1);
    release(JSON.stringify({ request_id: identity, outcome: "stopped" }));
    await stop;
    expect(text).toHaveBeenNthCalledWith(
      1,
      "Read-ahead text stays visible.",
      identity,
      "assistant",
    );
    expect(text).toHaveBeenNthCalledWith(
      2,
      " The remaining text stays readable.",
      identity,
      "assistant",
    );
    const next = {
      ...target,
      request_id: "55555555-5555-4555-8555-555555555555",
    };
    await started({
      callerIdentity: "ember-agent",
      payload: JSON.stringify(next),
    });
    expect(elements).toHaveLength(2);
    expect(elements[1]!.srcObject).not.toBeNull();
    expect(elements[0]!.srcObject).toBeNull();
    const finished = room.localParticipant.rpcHandlers.get(
      "ember.speech_finished",
    )!;
    changed.mockClear();
    await finished({
      callerIdentity: "ember-agent",
      payload: JSON.stringify(target),
    });
    expect(changed).not.toHaveBeenCalled();
    expect(elements).toHaveLength(2); // A late old finish never reattaches muted PCM.
    await finished({
      callerIdentity: "ember-agent",
      payload: JSON.stringify(next),
    });
    expect(changed).toHaveBeenCalledExactlyOnceWith(null);
    const before = room.localParticipant.performRpc.mock.calls.length;
    await client.stopSpeaking();
    expect(room.localParticipant.performRpc).toHaveBeenCalledTimes(before);
    await client.close();
  });

  it("rejects reply targets from another participant, character, context or instance", async () => {
    authorizeRoom();
    const state = configuredState(true);
    const changed = vi.fn();
    const client = new CharacterConnection(vi.fn(), vi.fn(), vi.fn(), changed);
    const room = roomOf(client);
    room.localParticipant.performRpc.mockResolvedValue(
      JSON.stringify(state.binding),
    );
    await client.connect(state);
    const started = room.localParticipant.rpcHandlers.get(
      "ember.reply_started",
    )!;
    const target = {
      instance_id: state.binding.instance_id,
      character_id: state.binding.character_id,
      context_id: state.context_id,
      request_id: "44444444-4444-4444-8444-444444444444",
    };
    for (const field of ["instance_id", "character_id", "context_id"])
      await expect(
        started({
          callerIdentity: "ember-agent",
          payload: JSON.stringify({ ...target, [field]: target.request_id }),
        }),
      ).rejects.toThrow("Reply identity changed");
    await expect(
      started({ callerIdentity: "intruder", payload: JSON.stringify(target) }),
    ).rejects.toThrow("Reply identity changed");
    expect(changed).not.toHaveBeenCalled();
    await client.stopSpeaking();
    expect(room.localParticipant.performRpc).toHaveBeenCalledTimes(1);
    await client.close();
  });

  it("does not clear a replacement connection's UI when an old Stop receipt arrives", async () => {
    authorizeRoom();
    const state = configuredState(true);
    const changed = vi.fn();
    const client = new CharacterConnection(vi.fn(), vi.fn(), vi.fn(), changed);
    const room = roomOf(client);
    room.localParticipant.performRpc.mockResolvedValue(
      JSON.stringify(state.binding),
    );
    await client.connect(state);
    const target = {
      instance_id: state.binding.instance_id,
      character_id: state.binding.character_id,
      context_id: state.context_id,
      request_id: "44444444-4444-4444-8444-444444444444",
    };
    await room.localParticipant.rpcHandlers.get("ember.reply_started")!({
      callerIdentity: "ember-agent",
      payload: JSON.stringify(target),
    });
    let release!: (reply: string) => void;
    room.localParticipant.performRpc.mockImplementation(
      () =>
        new Promise<string>((resolve) => {
          release = resolve;
        }),
    );
    const stop = client.stopSpeaking();
    await client.close();
    changed.mockClear();
    release(
      JSON.stringify({ request_id: target.request_id, outcome: "stopped" }),
    );
    await stop;
    expect(changed).not.toHaveBeenCalled();
  });

  it("can stop a fresh notice while the previous Stop response is still in flight", async () => {
    authorizeRoom();
    const state = configuredState(true);
    const changed = vi.fn();
    const client = new CharacterConnection(vi.fn(), vi.fn(), vi.fn(), changed);
    const room = roomOf(client);
    room.localParticipant.performRpc.mockResolvedValue(
      JSON.stringify(state.binding),
    );
    await client.connect(state);
    const start = room.localParticipant.rpcHandlers.get("ember.reply_started")!;
    const target = {
      instance_id: state.binding.instance_id,
      character_id: state.binding.character_id,
      context_id: state.context_id,
      request_id: "44444444-4444-4444-8444-444444444444",
    };
    const replies: ((value: string) => void)[] = [];
    room.localParticipant.performRpc.mockImplementation(
      () =>
        new Promise<string>((resolve) => {
          replies.push(resolve);
        }),
    );
    await start({
      callerIdentity: "ember-agent",
      payload: JSON.stringify(target),
    });
    const oldStop = client.stopSpeaking();
    const next = {
      ...target,
      request_id: "55555555-5555-4555-8555-555555555555",
    };
    await start({
      callerIdentity: "ember-agent",
      payload: JSON.stringify(next),
    });
    const newStop = client.stopSpeaking();
    expect(newStop).not.toBe(oldStop);
    expect(replies).toHaveLength(2);
    changed.mockClear();
    replies[0]!(
      JSON.stringify({ request_id: target.request_id, outcome: "stopped" }),
    );
    await oldStop;
    expect(changed).not.toHaveBeenCalled();
    expect(client.stopSpeaking()).toBe(newStop);
    replies[1]!(
      JSON.stringify({ request_id: next.request_id, outcome: "stopped" }),
    );
    await newStop;
    expect(changed).toHaveBeenCalledExactlyOnceWith(null);
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
