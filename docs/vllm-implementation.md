# vLLM implementation and qualification

Status: selectable adapter implemented and qualified for the recorded serial
GGUF profile. Ollama remains the default because this artifact/profile is faster
there. The operator removed the old wait-until-Phase-13 adoption gate after Phase
10; one active generation and drain on cancellation remain intentional.

## Configuration and ownership

The gateway selects one installed upstream. CharacterCore, memory clients, tasks,
P0–P4 authentication/priorities/reservations and conservative context budgets keep
their existing contracts. `inference.upstream` contains neutral internal frames;
old Ollama imports remain compatible. There is no direct model fallback. Current Core messages and tool-definition/call
streams are covered. The existing bare `role="tool"` message shape carries no
OpenAI tool-call ID; generic OpenAI/harness tool-result transcripts are not
qualified by this adapter. Phase 11 must normalize and test that separate contract.

Add an explicit deployment profile after installing and qualifying the server:

```toml
[inference]
backend = "vllm"

[inference.vllm]
endpoint = "http://127.0.0.1:8000"
version = "0.31.0"
artifact_sha256 = "a4adabb80876639edf19db8f9f8197492fa5c26e82ee1fdb6f7dd2136d7c70c1"
profile_sha256 = "<verified static setup receipt SHA-256>"
api_key_environment = "EMBER_VLLM_TOKEN"
```

Use a separate model tag, runtime root and verified `model.expected_digest` for
qualification. Keep context/input/output budgets internally consistent. The
`services.ollama` table remains compatible and may be disabled in the vLLM profile.
Selecting vLLM never installs, starts, unloads or falls back to another model.
`doctor` reports selected configuration as unprobed rather than claiming readiness.

`EMBER_VLLM_TOKEN` is a dedicated random alphanumeric credential, referenced through
environment configuration rather than embedded in TOML or command arguments. It
must be present in both the external server launcher and the gateway process.
MCP's minimal explicit environment does not inherit it.

Windows uses a separate WSL2 Linux environment. The gateway HTTP client and Core
contain no WSL, CUDA, Windows path or Torch dependency. A future Linux or macOS
Ember host can use the same client contracts; this does not claim CUDA/plugin
support on macOS. External runner/device profiles need their own qualification.

## Pinned installation and shared weights

The qualified candidate is vLLM **0.31.0**, Torch **2.13.0+cu130**, GGUF **0.19.0**,
and plugin source **e454affe0ca23c9dc8754ec3033270cb30622ed5**. The published plugin
0.0.5 predates the necessary Qwen hybrid/IQ3_S changes; its version label alone is
insufficient. vLLM 0.32/0.33 remove config modules this plugin imports, so choosing
the newest packages without checking compatibility is not a working upgrade.

The desktop's existing Ubuntu 26.04/glibc 2.43 failed CUDA 13.0 compilation with
`rsqrt`/`rsqrtf` exception-specification errors. A separate **EmberVLLM Ubuntu 24.04**
WSL distribution provides the supported glibc 2.39/GCC 13 build/runtime. The existing
Ubuntu installation and Windows NVIDIA driver are retained. Only toolkit packages
are installed inside WSL, never a Linux GPU driver.

Setup steps, all explicit operator work:

1. Install verified uv 0.13.7 and a separate Python 3.12.16 environment in the
   Linux service profile. Keep this environment outside Ember's `.venv`.
2. Install vLLM/Torch from the CUDA 13.0 wheel channel. Pin the compiler components:
   `nvidia-cuda-nvcc==13.0.88`, `nvidia-cuda-crt==13.0.88`,
   `nvidia-nvvm==13.0.88`, `nvidia-cuda-cccl==13.0.85`,
   `nvidia-nvjitlink==13.0.88`. Floating newer compiler/CRT packages caused real
   compiler/header mismatches during setup; those failures were corrected.
3. Check out the exact plugin commit outside product source. Build with GCC/G++ 13,
   `CUDA_HOME` pointing at the environment's `nvidia/cu13` toolkit,
   `TORCH_CUDA_ARCH_LIST=8.6`, `MAX_JOBS=2` and local version suffix `e454affe`.
   The runtime wheel supplies `libcudart.so.13`; the development linker name
   `libcudart.so` must resolve to that installed library. FlashInfer JIT also needs
   `CUDA_HOME/lib64` resolving to `lib` and `lib/libcuda.so` resolving to the existing
   WSL driver library `/usr/lib/wsl/lib/libcuda.so.1`. These are development linker
   names, not replacement runtimes or drivers.
4. Install the resulting `vllm-gguf-plugin==0.0.5+e454affe` wheel. Record the complete
   installed package set. Install `pip==26.3` in this environment as well: the pinned
   server-info collector invokes pip, and otherwise returns HTTP 500. No plugin
   source is vendored into Ember.
5. Reuse the existing content-addressed Ollama GGUF under `/mnt/c/.../.ollama/` via
   a symlink named `sha256-<actual blob SHA>.gguf`. Verify its full checksum during
   setup. The 12,246,459,840-byte model is not downloaded or copied again. Removing
   the owning Ollama model/blob later invalidates this service asset.

The exact artifact is [published here](https://huggingface.co/0bserverx/Qwen3.8-27B-Heretic-GSQ-RCO-GGUF/blob/0402aaae0d590f7c05752b5f22cc58fd8373af6f/RVN-Qwen3.8-27B-Heretic-GSQ-RCO-IQ3_S-mtp.gguf).
Its header contains 64 backbone blocks plus one MTP block, 866 tensors and no
vision tensors. The initial profile disables MTP and maps 851 backbone tensors.
Do not convert it to another quantization and describe that as a matched run.

Download only these small assets from companion revision
`c2cb2cfb239f2964595bee87fcfda55ad5fe33dd` of
`0bserverx/Qwen3.8-27B-Heretic-Abliterated-Uncensored`:

| File | SHA-256 |
| --- | --- |
| config.json | 8df0808889e4a0988ba32200eae7b1faf692b810a1a56e7cb49f826c80ef9af0 |
| tokenizer_config.json | b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27 |
| tokenizer.json | 0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3 |
| chat_template.jinja | c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041 |
| generation_config.json | e70c136c1b78ddc1fb0905bac8e733a4dc448d4f852a5dd75143fffc70be550e |

The template matches the GGUF bytes; all merges and the meaningful vocabulary
match. The GGUF adds padding placeholders. This does not prove equality with
Ollama's built-in renderer `qwen3.8` / parser `qwen3.5` or its actual MTP execution.

## Static receipt and guarded launch

The plugin incorrectly replaces this text-only configuration's CausalLM class
with ConditionalGeneration. An actual CPU probe reproduced that failure. The
pinned vLLM development option resolves the class without modifying plugin code:

```text
--model-class-overrides {"Qwen3_5ForConditionalGeneration":"vllm.model_executor.models.qwen3_5:Qwen3_5ForCausalLM"}
```

CPU class resolution and backbone tensor mapping passed with that override.
It remains an explicit experimental setting requiring actual model qualification.
The qualified arguments use bfloat16, one sequence/GPU, 512-token chunked prefill, prefix
caching, explicit matching tokenizer/config/template, no speculative decoder,
`qwen3_coder` tools, `qwen3` reasoning and per-request metrics. Preserve EOS IDs
`[248046,248044]` and the adapter's explicit stop/thinking mapping. Request logging
is disabled by vLLM's default; the launcher does not permit enabling it.

Create a private receipt with the installed GPU environment's Python:

```sh
python scripts/prepare_vllm_profile.py \
  --model /absolute/service/models/sha256-a4adabb80876639edf19db8f9f8197492fa5c26e82ee1fdb6f7dd2136d7c70c1.gguf \
  --metadata /absolute/service/metadata \
  --cuda-home /absolute/service/venv/lib/python3.12/site-packages/nvidia/cu13 \
  --cc /usr/bin/gcc-13 --cxx /usr/bin/g++-13 \
  --output /absolute/service/profile-new.json
```

The helper verifies the exact qualified GGUF, the five companion metadata files,
critical package versions and installed compiled plugin. It records exact argv,
all installed distribution versions plus Python, model/metadata/plugin bytes,
launcher/guard/profile and inference source checksums, compiler executables and
qualified execution environment. It performs no downloads, launch or weight copy;
output creation is exclusive and private. The metadata directory must contain
exactly the five approved files. Unlisted optional tokenizer files or directories
must not silently change the setup. `vllm_profile.load_profile` verifies the receipt
and computes its canonical SHA-256 before launch. Credentials never enter it.
Keep machine paths and receipts in ignored local storage; committed evidence uses
public artifacts and sanitized provenance.

Run `python scripts/run_vllm.py --profile <receipt.json>` from the external Linux
environment. The launcher replaces itself with the pinned installed server. It
checks canonical flag names, exact loopback binding and mandatory middleware,
rejects abbreviated/underscore variants and inline credentials, overrides inherited
kernel/frontend settings and enforces offline assets. The guard also checks the
executed argv/environment against the receipt. Input checksum verification adds
cold startup work, especially over the WSL/NTFS shared model path; it is not warm
turn latency.

The mandatory `vllm_gateway_guard.GatewayOnlyMiddleware` authenticates health,
version, engine metadata, model inventory, the setup-receipt hash and chat generation.
All other HTTP paths/methods and WebSockets are denied, including authenticated
sleep/RPC/cache/admin requests. Development mode is needed for `/server_info`;
vLLM's native API-key middleware alone does **not** protect it or dev controls.
There is no browser-facing CORS workflow for this service.

With the external server ready, use a temporary explicit deployment pin and run
`python scripts/inspect_vllm_pin.py --config <vllm-deployment.toml>`. This makes
bounded authenticated metadata requests only, verifies the complete supported
profile and prints the actual processing digest. It does not edit configuration,
generate text or bless a changed existing job. Set that digest explicitly before
starting the isolated gateway or changing the production profile.

## Processing identity and memory jobs

A versioned stable projection plus the static setup receipt binds weights,
metadata/template/parser, code/software/environment, precision/cache policy,
parallel shape, kernels and structured-output settings. Worker instance IDs,
ports, allocation observations and compilation counters are excluded. A separate
instance attestation fences restarts during generation. Unsupported distributed,
MTP/LoRA/transfer configurations fail closed pending their own qualification.

The vLLM processing digest differs from Ollama's manifest digest. Existing immutable
extraction jobs must not resume silently under it; use existing reevaluation/new-job
workflows with explicit profile approval. P0 voice, P1 assistance, P2 task inference
and P3/P4 extraction still share the existing admission, priority and reservation
owner. This adapter introduces no memory schema, extraction policy, authority or
new scheduler. Bigger runner context never enlarges CharacterCore's fixed total
prompt allowance or appends the whole conversation.

## Memory and later scheduling

The initial comparison preserves the existing 131,072-token runner capacity, not
an enlarged chat prompt. Its single-sequence profile reserves substantial cache
on the 24GB card; it is not a multi-model residency result. vLLM preallocates cache
from its configured GPU budget, so lowering `max_model_len` alone must not be
presented as freeing enough VRAM for a second model. A smaller-context experiment
also needs an explicit lower GPU/KV allocation, a new receipt and processing pin,
then the same gateway/voice benchmarks and allocation tests. Phase 13 owns actual
contention, scheduling and cancellation qualification. No memory-policy or context
expansion is required to take advantage of a future qualified backend profile.

## Review and acceptance evidence

A separate reviewer read the original investigation/PLAN and implementation with
fresh reproductions. Findings were fixed in production code with regressions:

- Actual plugin metadata uses `model_weights` for the GGUF and `model` for the HF
  config directory; an invented fixture initially hid the mismatch.
- Runtime instance/compilation/port drift must not change durable job identity;
  static files/parser/environment still require a new profile when changed.
- Complete-looking JSON stopped at `length` cannot become a successful processing
  checkpoint. Structured/background/tool truncation now fails after attestation.
- Native dev endpoints bypass API-key protection; a mandatory authenticated
  allowlist now guards metadata and denies administration.
- Flexible CLI abbreviations/underscore aliases could bypass loopback/middleware
  checks; the qualified canonical flag allowlist rejects the real reproductions.
- Inherited CUDA/Triton and Rust/frontend options could change behavior under the
  same receipt; semantic environment is now pinned and enforced.
- Real full memory schemas failed native admission even though small JSON probes
  passed: vLLM's XGrammar compatibility check rejects `propertyNames` with
  schema-valued additional properties, then Guidance rejects that keyword. A
  schema-node-only grammar projection omits unsupported key-name guidance while
  the gateway validates the original complete schema before publishing output.
  Original request schemas and literal property/default/enum data stay intact;
  invalid extension keys still fail. Native CPU admission checks cover both full
  claim schema versions, alongside gateway positive/negative regressions.
- Added optional tokenizer metadata or installed entry-point packages could escape
  receipt verification; launch now checks exact metadata-directory and complete
  software inventories as well as hashes. Native startup exposed setuptools
  adding bundled vendor metadata to `sys.path`; scanning canonical virtualenv
  `purelib`/`platlib` roots keeps the actual installed inventory stable without
  ignoring new installed packages. Four regressions and a native CPU import
  reproduction cover that distinction.
- Benchmark cleanup could skip releasing its reservation after a health error,
  and cancellation could claim recovery after a failed next-turn probe. Cleanup
  always releases its own reservation; reports retain failure/cleanup completion
  state and require an actually completed, visible next P0 turn.

The independent code passes ran **153 focused tests**, Ruff/format and targeted
strict Pyright with no remaining must-fix finding for the inspected single-GPU,
serial, non-MTP profile. Earlier passes also exercised 164 transport/identity
checks, followed by **33 helper/guard tests** for the native vendor-metadata fix.
The final grammar-compatibility review added **120 vLLM/gateway/background tests**
and strict checks, with no remaining must-fix finding. The reviewer did not
perform GPU or performance qualification.
The final full macOS suite passed **918 / 12 skipped** (930 collected). Native
Windows full gate passed **922 / 8 skipped** (930 collected), including Ruff
format/check and strict Pyright. Real gateway measurements follow below. Skipped
hardware tests are not passes.

## Native timing and original real acceptance

The unchanged original Phase 6 extraction → review → seed → index/restart →
CharacterCore/LiveKit-hook recall test passed in **8.14 seconds**. The unchanged
Phase 7 native-acceptance and all-six paired-profile test passed in **45.31
seconds**, including its non-null prefill assertion. These are whole synthetic
test durations, not model-only extraction timings. All ten live
text/Unicode/thinking/tool/P1–P4/full-memory-schema cases passed as well.
[Recorded acceptance](benchmarks/2026-11-04-vllm-qualification/real-acceptance.json)
retains test-source hashes and native results.

The first Phase 7 attempt omitted prefill and failed its original assertion.
Source inspection resolved the mapping: vLLM's
[native prefill](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/v1/metrics/stats.py#L547)
and [reported native TTFT](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/entrypoints/generate/base/serving.py#L78)
use exactly the same `first_token_ts - scheduled_ts` interval and request
statistics; its [Prometheus prefill histogram](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/v1/metrics/loggers.py#L1163)
observes that value. The adapter now converts the reported milliseconds to the
existing native-frame nanoseconds. This is vLLM's own prefill wall-clock interval:
it excludes the initial scheduler queue and includes first-token work and
intervening preemptions. It is not pure GPU execution or an assertion of equivalence
to Ollama's `prompt_eval_duration`. Missing data stays unknown, reported zero stays
zero, and invalid/overflowing values fail closed. Legacy 0.2 responses still omit
the optional field. No original acceptance assertion was changed.

The benchmark manifest labels these definitions separately. The final independent
review verified **142 focused tests**, Ruff/format and strict Pyright with no
remaining must-fix. Earlier native failures and the complete first benchmark
run are retained in the paper trail rather than discarded.

## Live qualification and benchmark results

Both final seven-case suites completed **210/210 warm trials and 7/7 strict
consumer-detach/drain/completed-next-P0 probes per backend**. All cleanup completed.
[Full tables, definitions and raw reports](benchmarks/2026-11-04-vllm-qualification/)
record the exact configuration, source hashes, complete software/asset receipt,
original unchanged real acceptance, failed attempts and the first full vLLM run.

The refreshed short-turn comparison is **110.0ms first-visible p95 / 800.5ms
turn p95 / 72.48 median decode tokens/s** for Ollama, versus **378.9ms / 1679.0ms
/ 35.98** for vLLM. Sampled dedicated GPU peaks across the final suites were
**21,658MiB Ollama and 22,539MiB vLLM**. CPU Juniper 1.1x paced voice turn p95 was
**15.566s versus 15.673s**, with first-visible p95 **431.1ms versus 709.8ms**.
Thus vLLM is an available future backend, not the faster/lower-memory choice for
this exact artifact/profile on the current 3090. Ollama stays the default.

The native BF16 profile loaded **11.98GiB** of weights with the CUDA GDN decode
kernel, FlashAttention2, about **8.86GiB** available KV cache, one sequence and a
512-token prefill budget at GPU utilization 0.90. The initial FP16/0.87/2048 profile
failed 128K allocation (8.33GiB needed, 7.85GiB available); that failure was fixed
rather than disguising a smaller-capacity comparison. Cold asset checks/model
load are separate from warm measurements. Cache allocation still leaves little
space for another substantial model. The older
[Phase 10 Ollama baseline](phase-10-validation.md#recorded-gateway-configuration-and-results-2026-11-04)
is retained historical evidence; its larger global VRAM peak is not mixed into
this refreshed comparison.

A real same-profile idle restart also passed: the worker instance changed, the
processing pin stayed identical, health recovered and a new P0 reply completed.
No existing pin/job was relabelled. The obsolete failed 9GB setup candidate was
removed with diagnostics retained; the original Ubuntu distro and model blob
were preserved.

All results use synthetic inputs and the installed CPU speech engine. First
visible, native decode/prefill, full paced voice, drain and next-turn timing have
explicit definitions. Different renderers/samplers and unqualified Ollama draft
acceptance prevent a claim of identical prompt bytes/MTP execution. These trials
do not establish physical microphone/network/speaker latency, native GPU abort,
concurrent-model throughput or near 128K prompt quality. Phase 13 still owns actual
preemption and contention qualification.

Primary sources: [vLLM installation](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/),
[experimental GGUF support](https://docs.vllm.ai/en/latest/features/quantization/gguf/),
[pinned plugin](https://github.com/vllm-project/vllm-gguf-plugin/tree/e454affe0ca23c9dc8754ec3033270cb30622ed5),
[CUDA 13.0 host support](https://docs.nvidia.com/cuda/archive/13.0.3/cuda-installation-guide-linux/index.html),
[CUDA under WSL](https://docs.nvidia.com/cuda/wsl-user-guide/).
