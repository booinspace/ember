# Strata screenshot input

Vision is opt-in on Ember's existing bearer-authenticated `/coding/v1/chat/completions`.
The model still returns text and tool calls, not images.

## Enable on Miku

Miku's tracked `deployment.strata.toml` and `deployment.strata-mtp.toml` already
opt in and pin the reader and projector. For a rebuild or new profile:

1. Get the matching projector from the same [SC117 revision](https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF/tree/90a3350cd800ec1fe0d1660a38414ad9e985782f).
   Verify its checksum before pinning. Stock ISTA has a different file with the same name.

   ```powershell
   hf download SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF `
     mmproj-Qwen3.8-Flash-Next-BF16.gguf --revision 90a3350cd800ec1fe0d1660a38414ad9e985782f `
     --local-dir 'C:\Models\SC117-Flash-Next'
   Get-FileHash 'C:\Models\SC117-Flash-Next\mmproj-Qwen3.8-Flash-Next-BF16.gguf' -Algorithm SHA256
   ```

   Expected: `2e788f8c511d8093c7b43cb87b2fd7e14228340318057f8fb20c86df2efe2355`.
2. Copy the chosen runner JSON to a separate local input file. Keep its engine,
   text model and drafting settings; add the top-level `vision` object below.
   Its `model` must match the existing `--native` path. Start with the CPU reader
   to avoid extra GPU residency. Pinned Strata adds the engine's `--vision` flag.

   ```json
   {
     "vision": {
       "exe": "C:\\Users\\operator\\repos\\Strata-SC117\\terminal\\engine\\strata-vision.exe",
       "mmproj": "C:\\Models\\SC117-Flash-Next\\mmproj-Qwen3.8-Flash-Next-BF16.gguf",
       "model": "C:\\Models\\SC117-Flash-Next\\IQ3_S\\Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_S-00001-of-00002.gguf",
       "gpu": false,
       "max_tokens": 300
     }
   }
   ```

3. Pass that copy to `scripts/prepare_strata_profile.py` as in [Windows setup](strata.md#rebuild-on-windows).
   It creates `runner.ember-strata-vision.json` and `deployment.strata-vision.toml`
   without overwriting text profiles. Carry over the intended HTTPS settings and
   runtime/data roots; keep the new runner pins. Set `coding_api = true` and
   `coding_vision = true` under `[inference]`. Keep `preemption_enabled = false`
   under `[gateway]`. Stop the old runtime before switching profiles. Ember always
   rehashes the reader and projector, even when text GGUF checksums are off.
4. With the coding bearer, check `/coding/v1/models` for
   `architecture.input_modalities: ["text", "image"]`. Then send a synthetic PNG
   containing a distinctive label not mentioned in the prompt. Use a user `content`
   array with `{"type":"text","text":"Read the label."}` and
   `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`.
   Check the answer against the image. Metadata alone does not prove an encode works.

Use inline PNG or JPEG only: up to four images, 512 KiB decoded and 4 million
pixels per image, within a 1 MiB JSON body. No remote URLs, paths or animated images.
`detail` does not override the pinned reader's token cap. Strata counts actual
image tokens before generation; replayed attachments count toward the request limits.

## Pass screenshots through 9Router

Keep **OpenAI Compatible / Chat Completions**, base URL `https://ember.example.test/coding/v1`,
and the Ember coding bearer. Send `ember/qwen3.8-flash-next-iq3_s` to 9Router;
Ember receives `qwen3.8-flash-next-iq3_s`.

In [9Router v0.5.119 (`1855aa4`)](https://github.com/decolua/9router/commit/1855aa44473e88feac9d3f131b9c0c5fc9706b68),
[chat routing](https://github.com/decolua/9router/blob/1855aa44473e88feac9d3f131b9c0c5fc9706b68/open-sse/handlers/chatCore.js#L175-L193)
strips images from this model by default, even inline data URLs. Register
`vision: true` for the resolved `openai-compatible-chat-…` node ID and
`qwen3.8-flash-next-iq3_s` in
[`open-sse/providers/capabilities.js`](https://github.com/decolua/9router/blob/1855aa44473e88feac9d3f131b9c0c5fc9706b68/open-sse/providers/capabilities.js#L839-L881)
(`PROVIDER_CAPABILITIES`), preserving the existing reasoning/tool capabilities
and using the deployed model's limits. That means patching and rebuilding this
version, or using a build that feeds saved custom-model caps into runtime checks.
Dashboard metadata or a display alias alone is insufficient. Repeat the
synthetic image test through 9Router before advertising image input to clients.

In OpenCode V2, set `capabilities.input` to `["text", "image"]` in the existing
`providers.9router.models.qwen3.8-flash-next-iq3_s` entry. Keep `capabilities.output`
as `["text"]` and retain alias, limits, tools and reasoning. If 9Router strips
images, use a separate direct OpenCode provider for Ember's HTTPS coding endpoint.
`media.image.max_base64_bytes` of `699000` can help fit a single screenshot;
history still counts toward the total JSON cap. Use synthetic images for smoke
checks. Avoid sending sensitive screenshots through a router configured to log
request bodies.

## Image generation is separate

Flash-Next does not generate images. Strata v0.2.18 supports a separate `sd-cli`
backend (such as Qwen-Image) at its native `/v1/images/generations`. Ember does
not expose it. Adding it needs a separate model ID, bounded requests and GPU
lifecycle coordination with chat and voice. Keep Strata's native port private.
The reviewed 9Router compatible chat node does not forward image generation;
use a supported image provider or add a registered image adapter.
