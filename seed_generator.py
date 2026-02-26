"""
crs-claude-code seed generator module (bug-finding).

Thin launcher that delegates seed generation to a swappable AI agent.
The agent (selected via CRS_AGENT env var) handles: target analysis, seed
creation, and fuzzer management via libCRS.

To add a new agent, create a module in agents/ implementing setup() and run_bugfind().
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
logger = logging.getLogger("seed_generator")

# --- Configuration (from oss-crs framework environment variables) ---

TARGET = os.environ.get("OSS_CRS_TARGET", "")
HARNESS = os.environ.get("OSS_CRS_TARGET_HARNESS", "")
LANGUAGE = os.environ.get("FUZZING_LANGUAGE", "c")
SANITIZER = os.environ.get("SANITIZER", "address")
LLM_API_URL = os.environ.get("OSS_CRS_LLM_API_URL", "")
LLM_API_KEY = os.environ.get("OSS_CRS_LLM_API_KEY", "")

FUZZER_MODULE = os.environ.get("FUZZER_MODULE", "fuzzer")
FUZZ_TIME = int(os.environ.get("FUZZ_TIME", "3600"))
SUBMISSION_FLUSH_WAIT_SECS = int(os.environ.get("SUBMISSION_FLUSH_WAIT_SECS", "12"))

# Agent selection
CRS_AGENT = os.environ.get("CRS_AGENT", "claude_code")

# Framework directories
WORK_DIR = Path("/work")
CORPUS_DIR = WORK_DIR / "corpus"
CRASHES_DIR = WORK_DIR / "crashes"

# CRS utils instance (initialized in main())
crs = None


# --- Common infrastructure ---


def setup_source() -> Path | None:
    """Download source code and locate the project source directory."""
    # Ensure safe.directory is set system-wide so git works regardless of
    # file ownership (downloaded source may have different uid).
    subprocess.run(
        ["git", "config", "--system", "--add", "safe.directory", "*"],
        capture_output=True,
    )

    source_dir = WORK_DIR / "src"
    source_dir.mkdir(parents=True, exist_ok=True)

    try:
        crs.download_build_output("src", source_dir)
    except Exception as e:
        logger.error("Failed to download source: %s", e)
        return None

    # Locate the project directory: try "repo/" first, then any subdir with .git.
    project_dir = source_dir / "repo"
    if not project_dir.exists():
        for d in source_dir.iterdir():
            if d.is_dir() and (d / ".git").exists():
                project_dir = d
                break

    # If still no project_dir, use "repo/" or first subdir as fallback.
    if not project_dir.exists():
        subdirs = [d for d in source_dir.iterdir() if d.is_dir()]
        if subdirs:
            project_dir = subdirs[0]
        else:
            logger.error("No project directory found in %s", source_dir)
            return None

    return project_dir


def wait_for_fuzzer() -> bool:
    """Fail-fast DNS check for the fuzzer sidecar."""
    try:
        domain = crs.get_service_domain(FUZZER_MODULE)
        logger.info("Fuzzer sidecar '%s' resolved to %s", FUZZER_MODULE, domain)
        return True
    except RuntimeError as e:
        logger.error("Failed to resolve fuzzer domain for '%s': %s",
                     FUZZER_MODULE, e)
        return False


def load_agent(agent_name: str):
    """Dynamically load an agent module from the agents package."""
    module_name = f"agents.{agent_name}"
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        logger.error("Failed to load agent '%s': %s", agent_name, e)
        sys.exit(1)


# --- Main loop ---


def main():
    logger.info(
        "Starting seed generator: target=%s harness=%s agent=%s",
        TARGET, HARNESS, CRS_AGENT,
    )

    global crs
    crs = init_crs_utils()

    # Register shared directories for corpus and crashes.
    # This creates symlinks to OSS_CRS_SHARED_DIR so the fuzzer sidecar can access them.
    # Note: This must be done BEFORE register_submit_dir, which needs the symlinks to exist.
    crs.register_shared_dir(CORPUS_DIR, "corpus")
    crs.register_shared_dir(CRASHES_DIR, "crashes")
    logger.info("Registered shared directories: corpus=%s, crashes=%s", CORPUS_DIR, CRASHES_DIR)

    # Register POV (crashes) submission directory (daemon thread — blocks forever).
    threading.Thread(
        target=crs.register_submit_dir,
        args=(DataType.POV, CRASHES_DIR),
        daemon=True,
    ).start()
    logger.info("POV submission watcher started on %s", CRASHES_DIR)

    # Register corpus submission directory
    threading.Thread(
        target=crs.register_submit_dir,
        args=(DataType.SEED, CORPUS_DIR),
        daemon=True,
    ).start()
    logger.info("Seed submission watcher started on %s", CORPUS_DIR)

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

    if not wait_for_fuzzer():
        logger.error("Cannot proceed without fuzzer sidecar")
        sys.exit(1)

    # Run the agent for bug-finding — it will:
    # 1. Analyze the target and harness
    # 2. Generate intelligent seed inputs into CORPUS_DIR
    # 3. Start the fuzzer via libCRS
    # 4. Monitor for crashes
    agent_work_dir = WORK_DIR / "agent"
    agent_work_dir.mkdir(parents=True, exist_ok=True)

    agent.run_bugfind(
        source_dir=source_dir,
        harness=HARNESS,
        corpus_dir=CORPUS_DIR,
        crashes_dir=CRASHES_DIR,
        work_dir=agent_work_dir,
        fuzzer_module=FUZZER_MODULE,
        fuzz_time=FUZZ_TIME,
        language=LANGUAGE,
        sanitizer=SANITIZER,
    )

    # Wait for the submission daemon to flush before exiting.
    logger.info("Waiting for daemon to flush...")
    time.sleep(SUBMISSION_FLUSH_WAIT_SECS)


if __name__ == "__main__":
    main()
