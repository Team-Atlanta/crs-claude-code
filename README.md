# atlantis-claude-code

A [CRS](https://github.com/oss-crs) (Cyber Reasoning System) that uses [Claude Code](https://docs.anthropic.com/en/docs/claude-code) to autonomously find and patch vulnerabilities in open-source projects.

Given proof-of-vulnerability (POV) inputs that crash a target binary, the agent analyzes the crashes, edits source code, builds, tests, iterates, and submits a verified patch — all autonomously.

## How it works

```
POV files → reproduce crashes → Claude Code agent → .diff patch
                                       ↕
                                 libCRS (build & test via builder sidecar)
```

1. **`run_patcher`** scans for POV files (all present before container starts) and reproduces all crashes against the unpatched binary.
2. All POVs are batched as variants of the same vulnerability and handed to the **agent** (selected via `CRS_AGENT` env var) in a single session.
3. The agent autonomously analyzes the vulnerability, edits source, and uses **libCRS** tools to build and test patches through a builder sidecar container — verifying against all POV variants.
4. A verified `.diff` is written to `/patches/`, where a daemon auto-submits it.

The agent is language-agnostic — it edits source and generates diffs while the builder sidecar handles compilation. The sanitizer type (address, memory, undefined) is passed to the agent for context.

## Project structure

```
bin/
  run_patcher          # Thin launcher: scan POVs → agent
  compile_target       # Builder phase: compiles the target project
agents/
  claude_code.py       # Claude Code agent (default)
  claude_code.md       # CLAUDE.md template with libCRS tool docs
  template.py          # Stub for creating new agents
oss-crs/
  crs.yaml             # CRS metadata (supported languages, models, etc.)
  example-compose.yaml # Example crs-compose configuration
  base.Dockerfile      # Base image: Ubuntu + Node.js + Claude Code CLI + Python
  builder.Dockerfile   # Build phase image
  patcher.Dockerfile   # Run phase image
  docker-bake.hcl      # Docker Bake config for the base image
  sample-litellm-config.yaml  # LiteLLM proxy config template
```

## Prerequisites

- **[oss-crs](https://github.com/oss-crs/oss-crs)** — the CRS framework (`crs-compose` CLI)

Builder sidecars for incremental builds are declared in `oss-crs/crs.yaml` (`snapshot: true` / `use_snapshot: true`) and handled automatically by the framework — no separate builder setup is needed.

## Quick start

### 1. Configure `crs-compose.yaml`

Copy `oss-crs/example-compose.yaml` and update the paths:

```yaml
atlantis-claude-code:
  source:
    local_path: /path/to/crs-claude-code
  cpuset: "2-7"
  memory: "16G"
  llm_budget: 10
  additional_env:
    CRS_AGENT: claude_code
    ANTHROPIC_MODEL: claude-sonnet-4-5-20250929

llm_config:
  litellm_config: /path/to/sample-litellm-config.yaml
```

### 2. Configure LiteLLM

Copy `oss-crs/sample-litellm-config.yaml` and set your API credentials. The LiteLLM proxy routes Claude Code's API calls to the Anthropic API (or your preferred provider). All models in `required_llms` must be configured. By default all model env vars point to the same model for benchmarking, but if you set individual tiers to different models, those must also be available in the proxy.

### 3. Run with oss-crs

```bash
crs-compose up -f crs-compose.yaml
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `CRS_AGENT` | `claude_code` | Agent module name (maps to `agents/<name>.py`) |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5-20250929` | Primary Claude model (also passed via `--model`) |
| `CLAUDE_CODE_SUBAGENT_MODEL` | `claude-sonnet-4-5-20250929` | Model for Claude Code subagents |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | `claude-sonnet-4-5-20250929` | Model the "opus" alias maps to |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `claude-sonnet-4-5-20250929` | Model the "sonnet" alias maps to |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `claude-sonnet-4-5-20250929` | Model the "haiku" alias maps to |
| `AGENT_TIMEOUT` | `0` (no limit) | Agent timeout in seconds (0 = run until budget exhausted) |

All model env vars are set to the same value by default for consistent benchmarking. These are standard Claude Code env vars — set them to different models in `additional_env` if you want mixed-model runs.

Available models:
- `claude-opus-4-6`
- `claude-opus-4-5-20251101`
- `claude-opus-4-1-20250805`
- `claude-sonnet-4-5-20250929`
- `claude-sonnet-4-20250514`
- `claude-haiku-4-5-20251001`

## Patch validity

A patch is submitted only when it meets all criteria:

1. **Builds** — compiles successfully
2. **POVs don't crash** — all POV variants pass
3. **Tests pass** — project test suite passes (or skipped if none exists)
4. **Semantically correct** — fixes the root cause with a minimal patch

## Adding a new agent

1. Copy `agents/template.py` to `agents/my_agent.py`.
2. Implement `setup()` and `run()`.
3. Set `CRS_AGENT=my_agent`.

The agent receives:
- **source_dir** — clean git repo of the target project
- **povs** — list of `(pov_path, crash_log)` tuples (variants of the same bug)
- **harness** — harness name for `run-pov`
- **patches_dir** — write verified `.diff` files here
- **work_dir** — scratch space
- **language** — target language (c, c++, java)
- **sanitizer** — sanitizer type (address, memory, undefined)

The agent has access to three libCRS commands:
- `libCRS apply-patch-build <patch.diff> <response_dir>` — build a patch
- `libCRS run-pov <pov> <response_dir> --harness <h> --build-id <id>` — test against a POV
- `libCRS run-test <response_dir> --build-id <id>` — run the project's test suite
