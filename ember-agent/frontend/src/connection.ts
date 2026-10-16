import { createRequestId, enableLanIds } from "./ids";
import { Room, RoomEvent, Track } from "livekit-client";
import { z } from "zod";
import {
  control,
  roomAccess,
  runtimeState,
  turnReceipt,
  type RuntimeState,
} from "./contracts";

export class CharacterConnection {
  readonly room: Room;
  private reads = new Set<Promise<void>>();
  private audio = new Set<HTMLMediaElement>();
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
  ) {
    enableLanIds();
    this.room = new Room({ adaptiveStream: true });
    this.room.registerTextStreamHandler(
      "lk.transcription",
      (reader, sender) => {
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
      },
    );
    this.room.on(RoomEvent.TrackSubscribed, (track, _, participant) => {
      if (
        !this.closed &&
        participant.identity === "ember-agent" &&
        track.kind === Track.Kind.Audio
      ) {
        const element = track.attach();
        this.audio.add(element);
        document.body.appendChild(element);
      }
    });
    this.room.on(RoomEvent.TrackUnsubscribed, (track) => {
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
      const reply = await this.room.localParticipant.performRpc({
        destinationIdentity: "ember-agent",
        method: "ember.ready",
        payload: "",
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
    if (result.request_id !== requestId || result.outcome !== "complete") {
      throw new Error(result.message ?? result.code ?? "Turn unavailable.");
    }
    return result.retained_memories;
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

  private removeAudio(): void {
    for (const element of this.audio) element.remove();
    this.audio.clear();
  }

  close(): Promise<void> {
    if (this.closing) return this.closing;
    this.closed = true;
    this.abort.abort();
    this.removeAudio();
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
