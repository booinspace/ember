import { createRequestId, enableLanIds } from "./ids";
import { Room, RoomEvent, Track, type RemoteTrack } from "livekit-client";
import { z } from "zod";
import {
  control,
  roomAccess,
  runtimeState,
  turnReceipt,
  type RuntimeState,
} from "./contracts";

const speechTarget = z.strictObject({
  instance_id: z.string().uuid(),
  character_id: z.string().uuid(),
  context_id: z.string().uuid(),
  request_id: z.string().uuid(),
});

export class CharacterConnection {
  readonly room: Room;
  private reads = new Set<Promise<void>>();
  private audio = new Set<HTMLMediaElement>();
  private audioTracks = new Set<RemoteTrack>();
  private state: RuntimeState | null = null;
  private activeRequest: string | null = null;
  private silencedRequest: string | null = null;
  private stop: { requestId: string; promise: Promise<void> } | null = null;
  private readonly abort = new AbortController();
  private closed = false;
  private closing: Promise<void> | null = null;

  constructor(
    private text: (
      value: string,
      segment: string,
      role: "user" | "assistant",
    ) => void,
    private failure: (message?: string) => void,
    private disconnected: () => void = () => undefined,
    private speechChanged: (requestId: string | null) => void = () => undefined,
  ) {
    enableLanIds();
    this.room = new Room({ adaptiveStream: true });
    for (const method of ["ember.reply_started", "ember.speech_finished"]) {
      this.room.localParticipant.registerRpcMethod(method, async (data) => {
        const value: unknown = JSON.parse(data.payload);
        const target = speechTarget.parse(value);
        if (
          this.closed ||
          data.callerIdentity !== "ember-agent" ||
          !this.state ||
          target.instance_id !== this.state.binding.instance_id ||
          target.character_id !== this.state.binding.character_id ||
          target.context_id !== this.state.context_id
        )
          throw new Error("Reply identity changed.");
        if (method === "ember.speech_finished") {
          if (this.activeRequest === target.request_id) {
            this.activeRequest = null;
            this.speechChanged(null);
          }
          return target.request_id;
        }
        this.activeRequest = target.request_id;
        this.speechChanged(target.request_id);
        if (target.request_id !== this.silencedRequest) {
          this.silencedRequest = null;
          for (const track of this.audioTracks) this.attachAudio(track);
        }
        return target.request_id;
      });
    }
    for (const topic of ["lk.transcription", "ember.reply"]) {
      this.room.registerTextStreamHandler(topic, (reader, sender) => {
        if (
          this.closed ||
          !["ember-agent", "ember-user"].includes(sender.identity) ||
          this.reads.size >= 2
        )
          return;
        // The hosted standard participant publishes speech transcripts itself.
        // Its SDK transcribed-track attribute identifies our current microphone.
        const track = reader.info.attributes?.["lk.transcribed_track_id"];
        const localSpeech =
          track !== undefined &&
          this.room.localParticipant.audioTrackPublications.has(track);
        const role =
          sender.identity === "ember-user" || localSpeech
            ? "user"
            : "assistant";
        const segment =
          reader.info.attributes?.["lk.segment_id"] ?? reader.info.id;
        if (
          topic === "ember.reply" &&
          (sender.identity !== "ember-agent" || localSpeech)
        )
          return;
        if (topic === "lk.transcription" && role === "assistant") return;
        const finalUser =
          role === "user" &&
          reader.info.attributes?.["lk.transcription_final"] === "true";
        if (role === "user" && !finalUser) return;
        const read = async (): Promise<void> => {
          try {
            let size = 0;
            const signal = AbortSignal.any([
              this.abort.signal,
              AbortSignal.timeout(190000),
            ]);
            for await (const part of reader.withAbortSignal(signal)) {
              size += new TextEncoder().encode(part).length;
              if (size > 131072) throw new Error("Output exceeds its bound.");
              this.text(part, segment, role);
            }
          } catch {
            if (!this.closed) this.failure();
          }
        };
        const task = read();
        this.reads.add(task);
        void task.then(
          () => this.reads.delete(task),
          () => this.reads.delete(task),
        );
      });
    }
    this.room.on(RoomEvent.TrackSubscribed, (track, _, participant) => {
      if (
        !this.closed &&
        participant.identity === "ember-agent" &&
        track.kind === Track.Kind.Audio
      ) {
        this.audioTracks.add(track);
        if (!this.silencedRequest) this.attachAudio(track);
      }
    });
    this.room.on(RoomEvent.TrackUnsubscribed, (track) => {
      this.audioTracks.delete(track);
      for (const element of track.detach()) {
        this.audio.delete(element);
        element.remove();
      }
    });
    this.room.on(RoomEvent.Disconnected, () => {
      const unexpected = !this.closed;
      this.closed = true;
      this.abort.abort();
      this.removeAudio();
      this.audioTracks.clear();
      if (unexpected) this.disconnected();
    });
  }

  async connect(state: RuntimeState): Promise<void> {
    let stage: "authorization" | "join" | "readiness" = "authorization";
    try {
      const access = await control("room-token", roomAccess, {});
      if (this.closed) throw new Error("Connection closed.");
      stage = "join";
      await this.room.connect(access.endpoint, access.token);
      if (this.closed) {
        await this.room.disconnect();
        throw new Error("Connection closed.");
      }
      stage = "readiness";
      this.state = state;
      const reply = await this.room.localParticipant.performRpc({
        destinationIdentity: "ember-agent",
        method: "ember.ready",
        payload: state.voice ? JSON.stringify({ speech_control: true }) : "",
      });
      const value: unknown = JSON.parse(reply);
      const binding = runtimeState.shape.binding.parse(value);
      if (
        binding.instance_id !== state.binding.instance_id ||
        binding.character_id !== state.binding.character_id
      ) {
        throw new Error("Room identity changed.");
      }
    } catch {
      if (this.closing) throw new Error("Connection closed.");
      const messages = {
        authorization:
          "Room authorization failed. Reopen the activation link from the running Ember instance.",
        join: "LiveKit room is unavailable. Restart Ember and reopen its activation link.",
        readiness:
          "The Ember worker is unavailable in this room. Restart Ember before reconnecting.",
      };
      throw new Error(messages[stage]);
    }
    if (state.voice) {
      try {
        await this.room.startAudio();
      } catch {
        this.failure(
          "Audio playback is blocked. Text chat is ready; use the microphone button to enable audio.",
        );
      }
    }
  }

  async ask(text: string): Promise<number> {
    const requestId = createRequestId();
    const reply = await this.room.localParticipant.performRpc({
      destinationIdentity: "ember-agent",
      method: "ember.turn",
      payload: JSON.stringify({ request_id: requestId, text }),
      responseTimeout: 190000,
    });
    const value: unknown = JSON.parse(reply);
    const result = turnReceipt.parse(value);
    if (!this.closed && this.activeRequest === requestId) {
      this.activeRequest = null;
      this.speechChanged(null);
    }
    if (
      result.request_id !== requestId ||
      (result.outcome !== "complete" && result.code !== "speech_stopped")
    ) {
      throw new Error(result.message ?? result.code ?? "Turn unavailable.");
    }
    return result.retained_memories;
  }

  /** Stop one acknowledged reply while retaining microphone, room and visible text. */
  stopSpeaking(): Promise<void> {
    const requestId = this.activeRequest;
    if (this.closed || !this.state || !requestId) return Promise.resolve();
    if (this.stop?.requestId === requestId) return this.stop.promise;
    this.silencedRequest = requestId;
    // Detach the owned playback sink immediately. The microphone, subscribed
    // tracks and visible read-ahead stream remain live; old audio is never replayed.
    this.removeAudio();
    const target = {
      instance_id: this.state.binding.instance_id,
      character_id: this.state.binding.character_id,
      context_id: this.state.context_id,
      request_id: requestId,
    };
    const operation = (async () => {
      const reply = await this.room.localParticipant.performRpc({
        destinationIdentity: "ember-agent",
        method: "ember.stop_speaking",
        payload: JSON.stringify(target),
        responseTimeout: 190000,
      });
      const value: unknown = JSON.parse(reply);
      const result = z
        .strictObject({
          request_id: z.string().uuid(),
          outcome: z.enum(["stopped", "not_current"]),
        })
        .parse(value);
      if (result.request_id !== requestId)
        throw new Error("Speech interruption could not be verified.");
      if (!this.closed && this.activeRequest === requestId) {
        this.activeRequest = null;
        this.speechChanged(null);
      }
    })();
    this.stop = { requestId, promise: operation };
    void operation
      .finally(() => {
        if (this.stop?.promise === operation) this.stop = null;
      })
      .catch(() => undefined);
    return operation;
  }

  async microphone(enabled: boolean): Promise<void> {
    if (enabled) await this.room.startAudio();
    await this.room.localParticipant.setMicrophoneEnabled(enabled);
  }

  async answerInvestigation(identity: string, _query: string): Promise<void> {
    await control(
      "deep/" + identity + "/answer",
      z.object({ receipt: z.string() }),
      {},
    );
  }

  private attachAudio(track: RemoteTrack): void {
    if (track.attachedElements.some((element) => this.audio.has(element)))
      return;
    const element = track.attach();
    this.audio.add(element);
    document.body.appendChild(element);
  }

  private removeAudio(): void {
    for (const track of this.audioTracks) {
      for (const element of track.detach()) {
        element.pause();
        element.srcObject = null;
        element.remove();
        this.audio.delete(element);
      }
    }
    for (const element of this.audio) {
      element.pause();
      element.srcObject = null;
      element.remove();
    }
    this.audio.clear();
  }

  close(): Promise<void> {
    if (this.closing) return this.closing;
    this.closed = true;
    this.abort.abort();
    this.removeAudio();
    this.audioTracks.clear();
    this.closing = (async () => {
      try {
        await this.room.disconnect();
      } finally {
        await Promise.allSettled([...this.reads]);
      }
    })();
    return this.closing;
  }
}
