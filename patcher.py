"""
crs-claude-code patcher module.

Thin launcher that delegates vulnerability fixing to a swappable AI agent.
The agent (selected via CRS_AGENT env var) handles: bug analysis, code editing,
building (via libCRS), testing (via libCRS), iteration, and final patch
submission (writing .diff to /patches/).

To add a new agent, create a module in agents/ implementing setup() and run().
"""

import importlib
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from libCRS.base import DataType
from libCRS.cli.main import init_crs_utils

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("patcher")

# --- Configuration (from oss-crs framework environment variables) ---

SNAPSHOT_IMAGE = os.environ.get("OSS_CRS_SNAPSHOT_IMAGE", "")
TARGET = os.environ.get("OSS_CRS_TARGET", "")
HARNESS = os.environ.get("OSS_CRS_TARGET_HARNESS", "")
LANGUAGE = os.environ.get("FUZZING_LANGUAGE", "c")
SANITIZER = os.environ.get("SANITIZER", "address")
LLM_API_URL = os.environ.get("OSS_CRS_LLM_API_URL", "")
LLM_API_KEY = os.environ.get("OSS_CRS_LLM_API_KEY", "")

BUILDER_MODULE = os.environ.get("BUILDER_MODULE", "inc-builder-asan")

# Agent selection
CRS_AGENT = os.environ.get("CRS_AGENT", "claude_code")

# Crash log truncation limit (keep the tail — ASAN summaries are at the end)
MAX_CRASH_LOG_CHARS = 16384

# Framework directories
WORK_DIR = Path("/work")
PATCHES_DIR = Path("/patches")
POV_DIR = WORK_DIR / "povs"
DIFF_DIR = WORK_DIR / "diffs"

# CRS utils instance (initialized in main())
crs = None


# --- Common infrastructure ---


def _reset_source(source_dir: Path) -> None:
    """Reset source directory to HEAD, cleaning up stale lock files."""
    for lock_file in source_dir.glob(".git/**/*.lock"):
        logger.warning("Removing stale lock file: %s", lock_file)
        lock_file.unlink()

    subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        cwd=source_dir, capture_output=True, timeout=60,
    )
    subprocess.run(
        ["git", "clean", "-fd"],
        cwd=source_dir, capture_output=True, timeout=60,
    )


def setup_source() -> Path | None:
    """Download source code and locate the project source directory."""
    source_dir = WORK_DIR / "src"
    source_dir.mkdir(parents=True, exist_ok=True)

    try:
        crs.download_build_output("src", source_dir)
    except Exception as e:
        logger.error("Failed to download source: %s", e)
        return None

    project_dir = source_dir / "repo"
    if not project_dir.exists():
        for d in source_dir.iterdir():
            if d.is_dir() and (d / ".git").exists():
                project_dir = d
                break

    if not project_dir.exists() or not (project_dir / ".git").exists():
        logger.error("No git repo found in %s", source_dir)
        return None

    return project_dir


def wait_for_builder() -> bool:
    """Fail-fast DNS check for the builder sidecar.

    Full health polling is handled internally by ``crs.run_pov()`` /
    ``crs.apply_patch_build()`` (via ``_wait_for_builder_health``), so we
    only verify DNS resolution here to catch configuration errors early.
    """
    try:
        domain = crs.get_service_domain(BUILDER_MODULE)
        logger.info("Builder sidecar '%s' resolved to %s", BUILDER_MODULE, domain)
        return True
    except RuntimeError as e:
        logger.error("Failed to resolve builder domain for '%s': %s",
                      BUILDER_MODULE, e)
        return False


def reproduce_crash(pov_path: Path) -> str:
    """Reproduce crash via builder sidecar using the base (unpatched) build."""
    if not HARNESS:
        return "No harness configured"

    response_dir = WORK_DIR / f"pov-{pov_path.stem}" / "reproduce"
    response_dir.mkdir(parents=True, exist_ok=True)

    try:
        exit_code = crs.run_pov(pov_path, HARNESS, "base", response_dir, BUILDER_MODULE)
        logger.info("reproduce_crash run-pov exit code: %d", exit_code)

        pov_stderr = response_dir / "pov_stderr.log"
        if pov_stderr.exists():
            log = pov_stderr.read_text()
            if len(log) > MAX_CRASH_LOG_CHARS:
                log = "[...truncated...]\n" + log[-MAX_CRASH_LOG_CHARS:]
            return log

        return "No crash output captured"
    except Exception as e:
        return f"Error reproducing crash: {e}"


def load_agent(agent_name: str):
    """Dynamically load an agent module from the agents package."""
    module_name = f"agents.{agent_name}"
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        logger.error("Failed to load agent '%s': %s", agent_name, e)
        sys.exit(1)


def process_povs(pov_paths: list[Path], source_dir: Path, agent,
                  ref_diff: str | None = None) -> bool:
    """Process a batch of POV variants in a single agent session.

    All POVs are assumed to be variants of the same vulnerability.
    We reproduce all crashes, then hand the full set to the agent so it
    can produce a patch that fixes all of them.

    Returns True if a patch was produced.
    """
    povs = []
    for pov_path in pov_paths:
        logger.info("Reproducing crash for POV: %s", pov_path.name)
        crash_log = reproduce_crash(pov_path)
        logger.info("Crash log for %s:\n%s", pov_path.name, crash_log)
        povs.append((pov_path, crash_log))

    _reset_source(source_dir)

    agent_work_dir = WORK_DIR / "agent"
    agent_work_dir.mkdir(parents=True, exist_ok=True)

    agent.run(source_dir, povs, HARNESS, PATCHES_DIR, agent_work_dir,
              language=LANGUAGE, sanitizer=SANITIZER,
              builder=BUILDER_MODULE, ref_diff=ref_diff)

    _reset_source(source_dir)

    patches = list(PATCHES_DIR.glob("*.diff"))
    if patches:
        logger.info("Patch produced: %s", [p.name for p in patches])
        return True

    logger.warning("Agent did not produce a patch")
    return False


# --- Main loop ---


def main():
    logger.info(
        "Starting patcher: target=%s harness=%s agent=%s snapshot=%s",
        TARGET, HARNESS, CRS_AGENT, SNAPSHOT_IMAGE or "(none)",
    )

    if not SNAPSHOT_IMAGE:
        logger.error("OSS_CRS_SNAPSHOT_IMAGE is not set.")
        logger.error("Declare 'snapshot: true' in target_build_phase and 'use_snapshot: true' in crs_run_phase (crs.yaml).")
        sys.exit(1)

    global crs
    crs = init_crs_utils()

    # Register patch submission directory (daemon thread — blocks forever).
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(
        target=crs.register_submit_dir,
        args=(DataType.PATCH, PATCHES_DIR),
        daemon=True,
    ).start()
    logger.info("Patch submission watcher started")

    # Fetch POV files (one-shot — all POVs are present before container starts).
    pov_files_fetched = crs.fetch(DataType.POV, POV_DIR)
    logger.info("Fetched %d POV file(s) into %s", len(pov_files_fetched), POV_DIR)

    # Fetch diff files for delta mode (one-shot, optional).
    try:
        diff_files_fetched = crs.fetch(DataType.DIFF, DIFF_DIR)
        if diff_files_fetched:
            logger.info("Fetched %d diff file(s) into %s", len(diff_files_fetched), DIFF_DIR)
    except Exception as e:
        logger.warning("Diff fetch failed: %s — delta mode diffs unavailable", e)

    # Register Claude Code logs as shared dir for post-run analysis.
    claude_log_dir = Path.home() / ".claude"
    if claude_log_dir.is_symlink():
        claude_log_dir.unlink()
    elif claude_log_dir.exists():
        shutil.rmtree(claude_log_dir)
    try:
        crs.register_shared_dir(claude_log_dir, "claude-logs")
        logger.info("Claude Code logs shared at %s", claude_log_dir)
    except Exception as e:
        logger.warning("Failed to register claude-logs shared dir: %s", e)
        claude_log_dir.mkdir(parents=True, exist_ok=True)

    source_dir = setup_source()
    if source_dir is None:
        logger.error("Failed to set up source directory")
        sys.exit(1)

    logger.info("Source directory: %s", source_dir)

    # Load and configure agent
    agent = load_agent(CRS_AGENT)
    agent.setup(source_dir, {
        "llm_api_url": LLM_API_URL,
        "llm_api_key": LLM_API_KEY,
    })

    # All POVs are present before container starts (POV_DIR → FETCH_DIR/pov/).
    # Scan once, batch all variants into a single agent session.
    pov_files = sorted(f for f in POV_DIR.rglob("*") if f.is_file() and not f.name.startswith("."))

    if not pov_files:
        logger.warning("No POV files found in %s", POV_DIR)
        sys.exit(0)

    logger.info("Found %d POV(s): %s", len(pov_files), [p.name for p in pov_files])

    # Read reference diff if available (delta mode).
    ref_diff = None
    ref_diff_path = DIFF_DIR / "ref.diff"
    if DIFF_DIR.exists() and ref_diff_path.is_file():
        ref_diff = ref_diff_path.read_text()
        logger.info("Reference diff found (%d chars)", len(ref_diff))

    if not wait_for_builder():
        logger.error("Cannot proceed without builder sidecar")
        sys.exit(1)

    if process_povs(pov_files, source_dir, agent, ref_diff=ref_diff):
        # Wait for the submission daemon to flush (batch_time=10s) before exiting.
        logger.info("Patch submitted. Waiting for daemon to flush...")
        time.sleep(30)


if __name__ == "__main__":
    main()
