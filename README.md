# crs-claude-code

A [CRS](https://github.com/oss-crs) (Cyber Reasoning System) that uses [Claude Code](https://docs.anthropic.com/en/docs/claude-code) to autonomously find and patch vulnerabilities in open-source projects.

Given any boot-time subset of vulnerability evidence (POVs, bug-candidate reports, diff files, and/or seeds), the agent analyzes the inputs, edits source code, builds, tests, iterates, and writes one final patch for submission.

## How it works

```
┌─────────────────────────────────────────────────────────────────────┐
│ patcher.py (orchestrator)                                           │
│                                                                     │
│  1. Fetch startup inputs & source                                    │
│     crs.fetch(POV/BUG_CANDIDATE/DIFF/SEED)                           │
│     crs.download(src)                                                │
│         │                                                            │
│         ▼                                                            │
│  2. Launch Claude Code agent with fetched paths + CLAUDE.md          │
│     claude -p --dangerously-skip-permissions                        │
│       --append-system-prompt <rules>                                │
└─────────┬───────────────────────────────────────────────────────────┘
          │ stdin: prompt with startup evidence paths
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Claude Code (autonomous agent)                                      │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐                   │
│  │ Analyze  │───▶│   Fix    │───▶│   Verify     │                   │
│  │          │    │          │    │              │                   │
│  │ Read     │    │ Edit src │    │ apply-patch  │──▶ Builder        │
│  │ startup  │    │ git diff │    │   -build     │    sidecar        │
│  │ evidence │    │          │    │              │◀── build_id       │
│  └──────────┘    └──────────┘    │ run-pov ────│──▶ Builder        │
│                                  │   (all POVs)│◀── pov_exit_code  │
│                       ▲          │ run-test ───│──▶ Builder        │
│                       │          │             │◀── test_exit_code  │
│                       │          └──────┬───────┘                   │
│                       │                 │                           │
│                       └── retry ◀── fail?                           │
│                                         │ pass                      │
│                                         ▼                           │
│                              Write .diff to /patches/               │
└─────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────┐
│ patcher.py               │
│ submit(first patch) ───▶ oss-crs framework
└─────────────────────────┘
```

1. **`run_patcher`** fetches available startup inputs (`POV`, `BUG_CANDIDATE`, `DIFF`, `SEED`) once, downloads source, and passes the fetched paths to the agent.
2. The evidence is handed to **Claude Code** in a single session with generated `CLAUDE.md` instructions. No additional inputs are fetched after startup.
3. The agent autonomously analyzes evidence, edits source, and uses **libCRS** tools (`apply-patch-build`, `run-pov`, `run-test`) to iterate through the builder sidecar.
4. When the first final `.diff` is written to `/patches/`, the patcher submits that single file with `crs.submit(DataType.PATCH, patch_path)` and exits. Later patch files or modifications are ignored.

The agent is language-agnostic — it edits source and generates diffs while the builder sidecar handles compilation. The sanitizer type (`address` only in this CRS) is passed to the agent for context.

## Project structure

```
patcher.py             # Patcher module: one-time fetch of optional inputs → agent → first-patch submit
pyproject.toml         # Package config (run_patcher entry point)
bin/
  compile_target       # Builder phase: compiles the target project
agents/
  claude_code.py       # Claude Code agent (default)
  claude_code.md       # CLAUDE.md template with libCRS tool docs
  sections/            # Dynamic CLAUDE.md section partial templates
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

Builder sidecars for incremental builds are declared in `oss-crs/crs.yaml` (`snapshot: true` / `run_snapshot: true`) and handled automatically by the framework — no separate builder setup is needed.

## Quick start

### 1. Configure `crs-compose.yaml`

Copy `oss-crs/example-compose.yaml` and update the paths:

```yaml
crs-claude-code:
  source:
    local_path: /path/to/crs-claude-code
  cpuset: "2-7"
  memory: "16G"
  llm_budget: 10
  additional_env:
    CRS_AGENT: claude_code
    ANTHROPIC_MODEL: claude-opus-4-6

llm_config:
  # Optional: uncomment if you want OSS-CRS to inject an external LiteLLM endpoint.
  # litellm:
  #   mode: external
  #   external:
  #     url_env: EXTERNAL_LITELLM_API_BASE
  #     key_env: EXTERNAL_LITELLM_API_KEY
```

### 2. Optional LiteLLM setup

If you want OSS-CRS to inject an external LiteLLM endpoint, uncomment the `llm_config` block and make sure `EXTERNAL_LITELLM_API_BASE` and `EXTERNAL_LITELLM_API_KEY` are set. `oss-crs/sample-litellm-config.yaml` remains available as a reference template for LiteLLM-backed setups.

### 3. Run with oss-crs

```bash
crs-compose up -f crs-compose.yaml
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `CRS_AGENT` | `claude_code` | Agent module name (maps to `agents/<name>.py`) |
| `ANTHROPIC_MODEL` | unset | Primary Claude model read from the environment |
| `CLAUDE_CODE_SUBAGENT_MODEL` | unset | Optional model for Claude Code subagents |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | unset | Optional env override for the `opus` alias |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | unset | Optional env override for the `sonnet` alias |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | unset | Optional env override for the `haiku` alias |
| `AGENT_TIMEOUT` | `0` (no limit) | Agent timeout in seconds (0 = run until budget exhausted) |
| `BUILDER_MODULE` | `inc-builder` | Builder sidecar module name (must match a `run_snapshot` entry in crs.yaml) |
| `OSS_CRS_SNAPSHOT_IMAGE` | framework-provided | Required snapshot image reference used by patcher startup checks |

These are standard Claude Code env vars. The CRS reads whatever values you provide in `additional_env`; if you want reproducible benchmarking, set each one explicitly.

Available models:
- `claude-opus-4-6`
- `claude-opus-4-5-20251101`
- `claude-opus-4-1-20250805`
- `claude-sonnet-4-6`
- `claude-sonnet-4-5-20250929`
- `claude-sonnet-4-20250514`
- `claude-haiku-4-5-20251001`

## Runtime behavior

- **Execution**: `claude -p --dangerously-skip-permissions --append-system-prompt <rules>` (non-interactive, full permissions)
- **Instruction file**: `CLAUDE.md` generated per run in the target repo
- **LiteLLM proxy**: Configured via `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` env vars

Debug artifacts:
- Log directory: `/root/.claude` (registered via `register-log-dir`)
- Per-run logs: `/work/agent/claude_code_stdout.log`, `/work/agent/claude_code_stderr.log`
- Claude Code internal logs: `/root/.claude/projects/`

## Patch submission

The agent is instructed to satisfy these criteria before writing a patch:

1. **Builds** — compiles successfully
2. **POVs don't crash** — all provided POV variants pass (if POVs were provided)
3. **Tests pass** — project test suite passes (or skipped if none exists)
4. **Semantically correct** — fixes the root cause with a minimal patch

Runtime remains trust-based: the patcher does not re-run final verification. Once the first `.diff` is written to `/patches/`, the patcher submits that single file and exits. Submitted patches cannot be edited or resubmitted, so the agent should only write to `/patches/` when it considers the patch final.

## Adding a new agent

1. Copy `agents/template.py` to `agents/my_agent.py`.
2. Implement `setup()` and `run()`.
3. Set `CRS_AGENT=my_agent`.

The agent receives:
- **setup(source_dir, config)** config keys:
  - `llm_api_url` — optional LiteLLM base URL
  - `llm_api_key` — optional LiteLLM key
  - `claude_home` — path for Claude Code state/logs
- **source_dir** — clean git repo of the target project
- **pov_dir** — boot-time POV input directory (may be empty)
- **bug_candidate_dir** — boot-time bug-candidate directory (may be empty)
- **diff_dir** — boot-time diff directory (may be empty)
- **seed_dir** — boot-time seed directory (may be empty)
- **harness** — harness name for `run-pov`
- **patches_dir** — write exactly one final `.diff` here
- **work_dir** — scratch space
- **language** — target language (c, c++, jvm)
- **sanitizer** — sanitizer type (`address` only)
- **builder** — builder sidecar module name (keyword-only, required)

All optional inputs are boot-time only. The patcher fetches them once and passes directory paths to the agent; no new POVs, bug-candidates, diff files, or seeds appear during the run.

The agent has access to three libCRS commands (the `--builder` flag specifies which builder sidecar module to use):
- `libCRS apply-patch-build <patch.diff> <response_dir> --builder <module>` — build a patch
- `libCRS run-pov <pov> <response_dir> --harness <h> --build-id <id> --builder <module>` — test against a POV
- `libCRS run-test <response_dir> --build-id <id> --builder <module>` — run the project's test suite

For transparent diagnostics, always inspect response_dir logs:
- Build: `build.log`, `build_stdout.log`, `build_stderr.log`
- POV: `pov_stdout.log`, `pov_stderr.log`
- Test: `test_stdout.log`, `test_stderr.log`
