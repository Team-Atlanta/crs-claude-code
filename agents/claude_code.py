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

# Strip "anthropic/" prefix — LiteLLM uses it for routing, but the Claude CLI doesn't.
_raw_model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
CLAUDE_MODEL = _raw_model.removeprefix("anthropic/")

try:
    AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "3600"))
except ValueError:
    AGENT_TIMEOUT = 3600

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
        logger.info("Model: %s", CLAUDE_MODEL)
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

    logger.info("Agent setup complete")


def run(
    source_dir: Path,
    crash_log: str,
    pov_path: Path,
    harness: str,
    patches_dir: Path,
    work_dir: Path,
    language: str = "c",
) -> bool:
    """Launch Claude Code in agentic mode to autonomously fix the vulnerability.

    Writes crash log and CLAUDE.md (with concrete paths), then sends a prompt.
    Claude Code autonomously analyzes, edits, builds, tests, iterates, and
    writes the final .diff to patches_dir.

    Returns True if a patch file was produced in patches_dir.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    # Write crash log to a file so Claude can reference it
    crash_log_path = work_dir / "crash_log.txt"
    crash_log_path.write_text(crash_log)
    logger.info("Wrote crash log to %s", crash_log_path)

    # Write CLAUDE.md with concrete paths for this POV
    claude_md = CLAUDE_MD_TEMPLATE.format(
        language=language,
        work_dir=work_dir,
        pov_path=pov_path,
        harness=harness,
        patches_dir=patches_dir,
    )
    (source_dir / "CLAUDE.md").write_text(claude_md)

    prompt = f"Fix the vulnerability described in {crash_log_path}. See CLAUDE.md for available tools."

    cmd = [
        "claude",
        "-p",
        "-d", str(source_dir),
        "--dangerously-skip-permissions",
        "--model", CLAUDE_MODEL,
        "--verbose",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=source_dir,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=AGENT_TIMEOUT)
            logger.info("Claude Code exit code: %d", proc.returncode)
            if stdout:
                logger.info("Claude Code output:\n%s", stdout)
            if proc.returncode != 0 and stderr:
                logger.warning("Claude Code stderr:\n%s", stderr)
        except subprocess.TimeoutExpired:
            logger.warning("Claude Code timed out (%ds), killing process tree", AGENT_TIMEOUT)
            os.killpg(proc.pid, signal.SIGTERM)
            time.sleep(2)
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    except Exception as e:
        logger.error("Error running Claude Code: %s", e)
        return False

    # Check if agent produced any patch files
    patches = list(patches_dir.glob("*.diff"))
    if patches:
        logger.info("Agent produced %d patch(es): %s", len(patches), [p.name for p in patches])
        return True

    logger.info("Agent did not produce a patch")
    return False
