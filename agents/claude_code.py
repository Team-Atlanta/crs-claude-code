"""
Claude Code agent for autonomous vulnerability patching.

Implements the agent interface (setup / run) using Claude Code CLI
in agentic mode. Claude Code reads CLAUDE.md for workflow instructions,
then autonomously: analyzes the crash → edits source → builds via libCRS
→ tests via libCRS → iterates → writes final .diff to patches_dir.
"""

import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("agent.claude_code")


# 0 = no timeout (run until budget is exhausted)
try:
    AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "0"))
except ValueError:
    AGENT_TIMEOUT = 0

_TEMPLATE_PATH = Path(__file__).with_suffix(".md")
CLAUDE_MD_TEMPLATE = _TEMPLATE_PATH.read_text()


def setup(source_dir: Path, config: dict) -> None:
    """One-time agent configuration.

    - Sets Claude-specific env vars (ANTHROPIC_BASE_URL, AUTH_TOKEN, IS_SANDBOX)
    - Writes .claude.json config
    - Writes CLAUDE.md into source_dir with libCRS tool docs + workflow
    """
    llm_api_url = config.get("llm_api_url", "")
    llm_api_key = config.get("llm_api_key", "")

    os.environ["IS_SANDBOX"] = "1"

    if llm_api_url and llm_api_key:
        os.environ["ANTHROPIC_BASE_URL"] = llm_api_url
        os.environ["ANTHROPIC_AUTH_TOKEN"] = llm_api_key
        os.environ["ANTHROPIC_API_KEY"] = ""
        logger.info("Claude Code configured with LiteLLM proxy: %s", llm_api_url)
        logger.info("ANTHROPIC_MODEL: %s", os.environ.get("ANTHROPIC_MODEL", "(not set)"))
        logger.info("CLAUDE_CODE_SUBAGENT_MODEL: %s", os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL", "(not set)"))
        logger.info("ANTHROPIC_DEFAULT_OPUS_MODEL: %s", os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "(not set)"))
        logger.info("ANTHROPIC_DEFAULT_SONNET_MODEL: %s", os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "(not set)"))
        logger.info("ANTHROPIC_DEFAULT_HAIKU_MODEL: %s", os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "(not set)"))
    else:
        logger.warning("No LLM API URL/key set, Claude Code may not work")

    # Write Claude JSON config
    claude_config = {
        "numStartups": 0,
        "autoUpdaterStatus": "disabled",
        "userID": "-",
        "hasCompletedOnboarding": True,
        "lastOnboardingVersion": "1.0.0",
        "projects": {
            str(source_dir): {
                "hasTrustDialogAccepted": True,
                "hasCompletedProjectOnboarding": True,
            }
        },
    }
    claude_json = Path.home() / ".claude.json"
    claude_json.write_text(json.dumps(claude_config))
    claude_json.chmod(0o600)
    logger.info("Wrote Claude config to %s", claude_json)

    # Global gitignore so CLAUDE.md never leaks into patches
    global_gitignore = Path.home() / ".gitignore"
    global_gitignore.write_text("CLAUDE.md\n")
    subprocess.run(
        ["git", "config", "--global", "core.excludesFile", str(global_gitignore)],
        capture_output=True,
    )

    logger.info("Agent setup complete")


def run(
    source_dir: Path,
    povs: list[tuple[Path, str]],
    harness: str,
    patches_dir: Path,
    work_dir: Path,
    *,
    language: str = "c",
    sanitizer: str = "address",
    builder: str,
    ref_diff: str | None = None,
) -> bool:
    """Launch Claude Code in agentic mode to autonomously fix the vulnerability.

    povs is a list of (pov_path, crash_log) tuples — variants of the same bug.
    Writes all crash logs and CLAUDE.md (with concrete paths), then sends a prompt.
    Claude Code autonomously analyzes, edits, builds, tests, iterates, and
    writes the final .diff to patches_dir.

    Returns True if a patch file was produced in patches_dir.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    # Write each crash log to a file and build POV sections for CLAUDE.md
    pov_sections = []
    for i, (pov_path, crash_log) in enumerate(povs):
        crash_log_path = work_dir / f"crash_log_{i}.txt"
        crash_log_path.write_text(crash_log)
        logger.info("Wrote crash log to %s", crash_log_path)

        pov_sections.append(
            f"- POV: `{pov_path}` — crash log: `{crash_log_path}`\n"
            f"  Test: `libCRS run-pov {pov_path} <response_dir> --harness {harness} --build-id <build_id> --builder {builder}`"
        )

    pov_list = "\n".join(pov_sections)

    # Build optional diff section for delta mode
    if ref_diff:
        diff_section = (
            "\n## Reference Diff (Delta Mode)\n\n"
            "This diff shows the code change that introduced the vulnerability:\n\n"
            f"```diff\n{ref_diff}\n```\n"
        )
    else:
        diff_section = ""

    # Write CLAUDE.md with concrete paths for all POVs
    claude_md = CLAUDE_MD_TEMPLATE.format(
        language=language,
        sanitizer=sanitizer,
        work_dir=work_dir,
        harness=harness,
        patches_dir=patches_dir,
        pov_list=pov_list,
        pov_count=len(povs),
        builder=builder,
        diff_section=diff_section,
    )
    (source_dir / "CLAUDE.md").write_text(claude_md)

    prompt = (
        f"Fix the vulnerability. There are {len(povs)} POV variant(s) — "
        f"crash logs are in {work_dir}/crash_log_*.txt. See CLAUDE.md for tools and POV details."
    )

    debug_log = work_dir / "claude_debug.log"
    stdout_log = work_dir / "claude_stdout.log"
    stderr_log = work_dir / "claude_stderr.log"

    cmd = [
        "claude",
        "-p",
        "-d", str(source_dir),
        "--dangerously-skip-permissions",
        "--debug-file", str(debug_log),
    ]

    # Stream stdout/stderr to log files (full conversation is in $HOME/.claude)

    try:
        with open(stdout_log, "w") as out_f, open(stderr_log, "w") as err_f:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                text=True,
                cwd=source_dir,
                start_new_session=True,
            )
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
                proc.wait(timeout=AGENT_TIMEOUT or None)
                logger.info("Claude Code exit code: %d", proc.returncode)
            except subprocess.TimeoutExpired:
                logger.warning("Claude Code timed out (%ds), killing process tree", AGENT_TIMEOUT)
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    time.sleep(2)
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
    except Exception as e:
        logger.error("Error running Claude Code: %s", e)
        return False

    if proc.returncode != 0:
        logger.warning("Claude Code failed (rc=%d), see %s", proc.returncode, stderr_log)

    # Check if agent produced any patch files
    patches = list(patches_dir.glob("*.diff"))
    if patches:
        logger.info("Agent produced %d patch(es): %s", len(patches), [p.name for p in patches])
        return True

    logger.info("Agent did not produce a patch")
    return False
