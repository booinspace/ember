# Strata setup on Windows

Strata is a separate runner, not an Ollama plugin. Keep both installed and use
separate Ember deployment files. Stop the active Ember runtime before switching
files; unload its model if it is still resident. Do not run both large models on
one RTX 3090. Startup must use an installed runner, never download dependencies.

the tested Windows host (`nathalie@10.254.254.11`) has a 24 GiB RTX 3090 and about 59.4 GiB of
visible physical RAM. Ember-managed SC117 `IQ3_S` passed a 32K startup and
short generation test. The new profile defaults to 128K; full-context performance
is not qualified. Ollama import is not needed.

## Rebuild on Windows

1. Install Python 3.12 and `uv`. From the Ember checkout, run `uv sync --locked`
   and `.\scripts\setup_livekit.ps1`. Run setup and launch as the same Windows
   user; use an unelevated terminal unless setup explicitly needs elevation.
2. Install [Strata v0.2.18](https://github.com/Niko1221/Strata/releases/tag/v0.2.18).
   Download the two `IQ3_S` shards from [SC117](https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF)
   (tested revision `90a3350cd800ec1fe0d1660a38414ad9e985782f`). Keep both
   original filenames. Use a fresh Strata data folder so setup builds a pack
   for SC117 rather than reusing stock ISTA offsets. With the Hugging Face `hf`
   CLI installed, run from the Strata checkout:

   ```powershell
   hf download SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF `
     --include 'IQ3_S/*.gguf' --revision 90a3350cd800ec1fe0d1660a38414ad9e985782f `
     --local-dir 'C:\Models\SC117-Flash-Next'
   .\START-HERE.bat --setup --family qwen --model IQ3_S `
     --gguf-dir 'C:\Models\SC117-Flash-Next\IQ3_S' `
     --data-dir 'C:\Models\Strata-data-SC117' `
     --context 131072 --vision no --mtp off --host 127.0.0.1 `
     --port 18110 --no-browser --no-start --yes
   ```

3. From the Ember checkout, generate an offline, pinned profile. Use paths
   from the installed Strata checkout; no previous Ember deployment is needed.

   ```powershell
   $strata = 'C:\Users\operator\repos\Strata-SC117\terminal'
   .\.venv\Scripts\python.exe .\scripts\prepare_strata_profile.py `
     "$strata\strata-iq3_s.json" "$strata\.venv\Scripts\python.exe" `
     "$strata\serve\server.py"
   ```

   Use `--context 32768` to reproduce the shorter baseline. The helper creates
   `.local/config/deployment.strata.toml` and `runner.ember-strata.json` and
   refuses to overwrite existing files. Keep those files local. If elevated
   setup is necessary, ensure the account that launches Ember can read both;
   grant that account access, not all users.
4. Run `start.bat`, select `deployment.strata.toml`, then use `ember livekit-chat`
   with the same config from a second shell. The generated profile is text-only
   and loopback-only. Use Ctrl+C to shut down before selecting another profile.
   Launch starts only installed services. Ember checks the runner config,
   artifact paths, and non-GGUF checksums on every start. It does not rehash
   GGUF model files by default, so in-place corruption or replacement may
   go undetected. Set `verify_model_checksums = true` under `[inference.strata]`
   in the deployment TOML to rehash model files on every start.

## Share Strata with 9router

On a Strata deployment with configured browser HTTPS ingress, set
`coding_api = true` under `[inference]` and restart Ember. In 9router, add an
OpenAI Compatible provider using **Chat Completions**:

- Base URL: `https://ember.example.test/coding/v1` (use your configured HTTPS origin).
- Model ID: `qwen3.8-flash-next-iq3_s`.
- API key: contents of `coding-api.token` in this deployment's `runtime_root`,
  created on launch. Keep it private; never use a gateway or Strata key here.

Test `/coding/v1/models` first, then a streamed Chat Completions request. The
128K profile has 131,072 context tokens, with 122,880 available for the rendered
prompt and 8,192 reserved for output. For an existing 128K deployment, set
`max_input_tokens = 122880` and `output_reserve_tokens = 8192` under `[model]`.
Match any client model limits to those values. Strata counts the conversation
and tool definitions with the model's tokenizer before generating. If you
receive `context_length_exceeded`, compact the conversation or start a new one.

The coding route does not expose character memory or run tools. Ember and 9router share one
runner and one generation slot. A voice/text turn waits for any running coding
completion to drain, then goes before queued coding jobs. No preemption, extra
LAN port, or direct access to `18110` is required. This is Chat Completions,
not the Responses API. See [screenshot input and 9Router setup](strata-vision.md).

Coding gets 10 minutes per model request, including queue time, not per OpenCode
turn. Streaming sends keepalive comments during queueing and prefill. A proxy's
own total request timeout can still end a request sooner. After `deadline_exceeded`,
Ember gives the original Strata stream up to 30 more seconds to verify release.
The reply stays failed, no late tools are sent, and Ember does not replay the
request. Voice still waits behind active coding, with its own unchanged deadline.
If health reports `quiescence_required`, retries cannot clear it; follow the
[operator recovery procedure](../../docs/phase-7-runbook.md#L134-L136).

## Thinking in OpenCode and the browser

For direct OpenCode access, use `@opencode/ai/providers/openai-compatible`,
the HTTPS `/coding/v1` base URL, and Ember's coding API token. Use model ID
`qwen3.8-flash-next-iq3_s` without the 9router `ember/` prefix. OpenCode sends
`reasoning_effort` on Chat Completions. Ember also accepts Qwen-style
`enable_thinking` and known `thinking_budget` markers sent by proxies such
as 9router. Both forms use the same output cap. Choosing `none`, `off`, or
`minimal` disables thinking. For display, set the model's
`compatibility.reasoningField` to `reasoning_content`; any proxy must
preserve this response field.

For a voice turn, turn on **Thinking** in Ember's web UI and open **Turn
details**. Strata receives an explicit thinking request. The display stays in
the current turn, and LiveKit speaks the visible answer.

[Strata v0.2.18](https://github.com/Niko1221/Strata/releases/tag/v0.2.18),
released November 21, 2026, also adds native Tencent Hy4-Flash (220B) on
Windows CUDA. Strata supports specific architectures and quantizations, not
arbitrary GGUFs. Check each family's engine, pack, and hardware requirements
before downloading; support does not imply fit on the target host.
