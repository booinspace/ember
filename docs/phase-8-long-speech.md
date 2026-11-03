# Long spoken turns: phase 8 correction

The installed Silero VAD can retain its configured maximum plus prefix padding and
silently drops later samples when that buffer fills. Ember currently configures 30
seconds plus 0.5 seconds padding but rejects anything above 30 seconds. Long speech
therefore can be dropped and can terminate the recognition stream.

The audio adapter owns a five-minute VAD buffer with explicit half-second prefix
padding. The bounded STT worker accepts that envelope, transcribes consecutive
30-second slices in order, joins their text and submits a single final transcript.
Both local Whisper and the optional Phonon provider reuse the same worker and keep
one owned native task; cancellation does not release admission early. The aggregate
transcript is limited to 16,000 characters, matching character input. Timeouts grow
to accommodate multiple local CPU passes. No model generation, archive or storage
ownership changes.

This fixes speech lasting a minute or several minutes, including the original padding
boundary. It does not add unbounded speech: the existing SDK discards input after the
five-minute VAD buffer fills until the speaker pauses. Avoid continuous speech longer
than five minutes until incremental streaming is implemented. Tests must cover the
30-second padding boundary, a longer utterance with ordered complete chunks, maximum
size rejection and worker cancellation. Physical-device quality and latency remain
separate acceptance gates.

## CPU Phonon-2 speech input

The application can own an explicitly provisioned loopback Phonon-2 process. A
configured executable and local cache are optional and paired; if supplied with the
Phonon endpoint, runtime startup starts the installed CPU-only service without
network downloads, waits for verified model and CPU health, and shuts down only its
owned child. An already running endpoint is attached only after the same identity and
CPU check. Failure degrades voice instead of falling back to Whisper. Setup installs
the separate pinned Fermion runtime and verified model; user audio is never used for
setup. The optional attach-only profile remains available. Speech goes through the
existing VAD and bounded worker and does not use the primary model gateway.
