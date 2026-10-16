import { z } from "zod";
import { control, type RuntimeState } from "./contracts";
import { createRequestId, enableLanIds } from "./ids";

const lease = z.object({ session_id: z.string().uuid() });
const reply = z.object({
  request_id: z.string().uuid(),
  text: z.string().max(131072),
  retained_memories: z.number().int().nonnegative(),
});

export class TextConnection {
  private identity: string | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
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
    private disconnected: () => void,
  ) {
    enableLanIds();
  }

  async connect(state: RuntimeState): Promise<void> {
    if (!state.text_available || this.closed)
      throw new Error("Text chat is unavailable.");
    const value = await control("text/connect", lease, {}, this.abort.signal);
    this.identity = value.session_id;
    if (this.closed) {
      await this.disconnectLease();
      throw new Error("Connection closed.");
    }
    this.timer = setInterval(() => {
      void this.heartbeat();
    }, 15000);
  }

  private async heartbeat(): Promise<void> {
    if (!this.identity || this.closed) return;
    try {
      await control(
        "text/heartbeat",
        z.object({ connected: z.boolean() }),
        { session_id: this.identity },
        AbortSignal.any([this.abort.signal, AbortSignal.timeout(10000)]),
      );
    } catch {
      if (!this.closed) {
        this.failure("Text connection expired. Reconnect to continue.");
        this.disconnected();
        await this.close().catch(() => undefined);
      }
    }
  }

  async ask(text: string, investigationId?: string): Promise<number> {
    if (!this.identity || this.closed)
      throw new Error("Text connection is closed.");
    const requestId = createRequestId();
    const value = await control(
      "text/turn",
      reply,
      {
        session_id: this.identity,
        request_id: requestId,
        text,
        investigation_id: investigationId ?? null,
      },
      AbortSignal.any([this.abort.signal, AbortSignal.timeout(190000)]),
    );
    if (this.closed || value.request_id !== requestId)
      throw new Error("Reply was interrupted.");
    this.text(value.text, requestId, "assistant");
    await control(
      "text/acknowledge",
      z.object({ delivered: z.boolean() }),
      {
        session_id: this.identity,
        request_id: requestId,
      },
      this.abort.signal,
    );
    return value.retained_memories;
  }

  async answerInvestigation(identity: string, query: string): Promise<void> {
    await this.ask(query.slice(0, 1600), identity);
  }

  async microphone(_enabled: boolean): Promise<void> {
    throw new Error("Voice is unavailable. Text chat remains available.");
  }

  private async disconnectLease(): Promise<void> {
    const identity = this.identity;
    this.identity = null;
    if (identity)
      await control(
        "text/disconnect",
        z.object({ connected: z.boolean() }),
        { session_id: identity },
        AbortSignal.timeout(10000),
      );
  }

  close(): Promise<void> {
    if (this.closing) return this.closing;
    this.closed = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.abort.abort();
    this.closing = this.disconnectLease();
    return this.closing;
  }
}
