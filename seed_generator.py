"""
crs-claude-code seed generator module (bug-finding).

Workflow:
1. Start fuzzer in background (runs continuously)
2. Run Claude Code in a loop to generate unique seeds
3. Fuzzer automatically picks up new seeds from corpus directory

The agent (selected via CRS_AGENT env var) implements setup() and generate_seeds().
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

# Seed generation loop settings
SEED_GEN_INTERVAL = int(os.environ.get("SEED_GEN_INTERVAL", "30"))  # seconds between iterations
FUZZER_POLL_INTERVAL = int(os.environ.get("FUZZER_POLL_INTERVAL", "10"))  # seconds between status polls

# Agent selection
CRS_AGENT = os.environ.get("CRS_AGENT", "claude_code")

# Framework directories
WORK_DIR = Path("/work")
CORPUS_DIR = WORK_DIR / "corpus"      # Fuzzer's working corpus (actively managed by fuzzer)
SEEDS_DIR = WORK_DIR / "seeds"        # Claude's seeds (copied to corpus)
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


# Global flag to signal fuzzer status thread to stop
_fuzzer_running = True
# Global fuzzer handle (updated on restart)
_fuzzer_handle = None


def poll_fuzzer_status():
    """Background thread that polls fuzzer status every FUZZER_POLL_INTERVAL seconds."""
    global _fuzzer_running, _fuzzer_handle
    while _fuzzer_running:
        if _fuzzer_handle is None:
            time.sleep(FUZZER_POLL_INTERVAL)
            continue
        try:
            status = crs.fuzzer_status(_fuzzer_handle.fuzzer_id, FUZZER_MODULE)
            logger.info(
                "[fuzzer-poll] state=%s, execs=%d, corpus=%d, crashes=%d, runtime=%.0fs",
                status.state, status.execs, status.corpus_size, status.crashes_found,
                status.runtime_seconds,
            )
            if status.state != "running":
                logger.info("[fuzzer-poll] Fuzzer stopped (state=%s)", status.state)
                _fuzzer_running = False
                break
        except Exception as e:
            logger.warning("[fuzzer-poll] Failed to get status: %s", e)
        time.sleep(FUZZER_POLL_INTERVAL)


def restart_fuzzer():
    """Stop current fuzzer and start a new one with updated corpus."""
    global _fuzzer_handle

    # Stop current fuzzer if running
    if _fuzzer_handle is not None:
        try:
            result = crs.stop_fuzzer(_fuzzer_handle.fuzzer_id, FUZZER_MODULE)
            logger.info(
                "Stopped fuzzer %s: runtime=%.0fs, crashes=%d",
                _fuzzer_handle.fuzzer_id, result.runtime_seconds, result.crashes_found,
            )
        except Exception as e:
            logger.warning("Failed to stop fuzzer %s: %s", _fuzzer_handle.fuzzer_id, e)

    # Start new fuzzer
    _fuzzer_handle = crs.start_fuzzer(
        harness_name=HARNESS,
        corpus_dir=CORPUS_DIR,
        crashes_dir=CRASHES_DIR,
        fuzzer=FUZZER_MODULE,
        timeout=FUZZ_TIME,
    )
    logger.info("Started fuzzer: id=%s, pid=%d", _fuzzer_handle.fuzzer_id, _fuzzer_handle.pid)


# --- Main loop ---


def main():
    logger.info(
        "Starting seed generator: target=%s harness=%s agent=%s",
        TARGET, HARNESS, CRS_AGENT,
    )

    global crs
    crs = init_crs_utils()

    # Register shared directories for corpus, seeds, and crashes.
    # This creates symlinks to OSS_CRS_SHARED_DIR so the fuzzer sidecar can access them.
    # Note: This must be done BEFORE register_submit_dir, which needs the symlinks to exist.
    crs.register_shared_dir(CORPUS_DIR, "corpus")
    crs.register_shared_dir(SEEDS_DIR, "seeds")
    crs.register_shared_dir(CRASHES_DIR, "crashes")
    logger.info("Registered shared directories: corpus=%s, seeds=%s, crashes=%s",
                CORPUS_DIR, SEEDS_DIR, CRASHES_DIR)

    # Register POV (crashes) submission directory (daemon thread — blocks forever).
    threading.Thread(
        target=crs.register_submit_dir,
        args=(DataType.POV, CRASHES_DIR),
        daemon=True,
    ).start()
    logger.info("POV submission watcher started on %s", CRASHES_DIR)

    # Register seed submission directory.
    # NOTE: We watch SEEDS_DIR (Claude's seeds), NOT CORPUS_DIR (fuzzer's working directory).
    # The fuzzer actively manages CORPUS_DIR (creates/deletes files during minimization),
    # which would cause race conditions if we tried to submit from there.
    threading.Thread(
        target=crs.register_submit_dir,
        args=(DataType.SEED, SEEDS_DIR),
        daemon=True,
    ).start()
    logger.info("Seed submission watcher started on %s", SEEDS_DIR)

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

    # Start fuzzer FIRST (runs in background)
    logger.info("Starting fuzzer: harness=%s, timeout=%d", HARNESS, FUZZ_TIME)
    restart_fuzzer()

    # Start background thread to poll fuzzer status every 10 seconds
    threading.Thread(
        target=poll_fuzzer_status,
        daemon=True,
    ).start()
    logger.info("Fuzzer status polling thread started (interval=%ds)", FUZZER_POLL_INTERVAL)

    # Run Claude Code in a loop to generate seeds
    agent_work_dir = WORK_DIR / "agent"
    agent_work_dir.mkdir(parents=True, exist_ok=True)

    iteration = 0
    while _fuzzer_running:
        # Generate seeds into SEEDS_DIR (for submission)
        iteration += 1
        iter_work_dir = agent_work_dir / f"iter_{iteration}"
        iter_work_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Starting seed generation iteration %d", iteration)
        seeds_before = set(SEEDS_DIR.glob("*"))
        agent.generate_seeds(
            source_dir=source_dir,
            harness=HARNESS,
            seeds_dir=SEEDS_DIR,
            work_dir=iter_work_dir,
            iteration=iteration,
            language=LANGUAGE,
            sanitizer=SANITIZER,
        )

        # Copy new seeds to CORPUS_DIR for fuzzer to use
        seeds_after = set(SEEDS_DIR.glob("*"))
        new_seeds = seeds_after - seeds_before
        for seed in new_seeds:
            if seed.is_file():
                dest = CORPUS_DIR / seed.name
                if not dest.exists():
                    shutil.copy2(seed, dest)
                    logger.debug("Copied seed %s to corpus", seed.name)
        if new_seeds:
            logger.info("Copied %d new seeds to corpus", len(new_seeds))
            # Restart fuzzer so it picks up new seeds in fork mode
            restart_fuzzer()

        # Brief pause between iterations
        time.sleep(SEED_GEN_INTERVAL)

    # Wait for the submission daemon to flush before exiting.
    logger.info("Waiting for daemon to flush...")
    time.sleep(SUBMISSION_FLUSH_WAIT_SECS)


if __name__ == "__main__":
    main()
