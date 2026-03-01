"""
crs-claude-code patcher module.

Thin launcher that delegates vulnerability fixing to a swappable AI agent.
The agent (selected via CRS_AGENT env var) handles: bug analysis, code editing,
building (via libCRS), testing (via libCRS), iteration, and final patch
submission (writing .diff to /patches/).

To add a new agent, create a module in agents/ implementing setup() and run().
"""

import importlib
import inspect
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

SNAPSHOT_IMAGE = os.environ.get("OSS_CRS_SNAPSHOT_IMAGE", "")
TARGET = os.environ.get("OSS_CRS_TARGET", "")
HARNESS = os.environ.get("OSS_CRS_TARGET_HARNESS", "")
LANGUAGE = os.environ.get("FUZZING_LANGUAGE", "c")
SANITIZER = os.environ.get("SANITIZER", "address")
LLM_API_URL = os.environ.get("OSS_CRS_LLM_API_URL", "")
LLM_API_KEY = os.environ.get("OSS_CRS_LLM_API_KEY", "")

BUILDER_MODULE = os.environ.get("BUILDER_MODULE", "inc-builder-asan")
SUBMISSION_FLUSH_WAIT_SECS = int(os.environ.get("SUBMISSION_FLUSH_WAIT_SECS", "12"))

CRS_AGENT = os.environ.get("CRS_AGENT", "claude_code")

WORK_DIR = Path("/work")
PATCHES_DIR = Path("/patches")
POV_DIR = WORK_DIR / "povs"
DIFF_DIR = WORK_DIR / "diffs"
BUG_CANDIDATE_DIR = WORK_DIR / "bug-candidates"

crs = None


def _reset_source(source_dir: Path) -> None:
    """Reset source directory to HEAD, cleaning up stale lock files."""
    for lock_file in source_dir.glob(".git/**/*.lock"):
        logger.warning("Removing stale lock file: %s", lock_file)
        lock_file.unlink()

    reset_proc = subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        cwd=source_dir, capture_output=True, timeout=60,
    )
    clean_proc = subprocess.run(
        ["git", "clean", "-fd"],
        cwd=source_dir, capture_output=True, timeout=60,
    )
    if reset_proc.returncode != 0:
        stderr = reset_proc.stderr.decode(errors="replace") if isinstance(reset_proc.stderr, bytes) else str(reset_proc.stderr)
        raise RuntimeError(f"git reset failed: {stderr.strip()}")
    if clean_proc.returncode != 0:
        stderr = clean_proc.stderr.decode(errors="replace") if isinstance(clean_proc.stderr, bytes) else str(clean_proc.stderr)
        raise RuntimeError(f"git clean failed: {stderr.strip()}")


def _snapshot_patch_state() -> dict[str, tuple[int, int]]:
    """Capture patch file state by name -> (mtime_ns, size)."""
    state: dict[str, tuple[int, int]] = {}
    for p in PATCHES_DIR.glob("*.diff"):
        try:
            st = p.stat()
        except OSError:
            continue
        state[p.name] = (st.st_mtime_ns, st.st_size)
    return state


def setup_source() -> Path | None:
    """Download source code and locate the project source directory."""
    safe_dir_proc = subprocess.run(
        ["git", "config", "--system", "--add", "safe.directory", "*"],
        capture_output=True,
    )
    if safe_dir_proc.returncode != 0:
        fallback_proc = subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", "*"],
            capture_output=True,
        )
        if fallback_proc.returncode != 0:
            logger.warning(
                "Failed to configure git safe.directory in both --system and --global scopes"
            )

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

    if not project_dir.exists():
        subdirs = sorted(
            (d for d in source_dir.iterdir() if d.is_dir()),
            key=lambda p: p.name,
        )
        if subdirs:
            project_dir = subdirs[0]
        else:
            logger.error("No project directory found in %s", source_dir)
            return None

    if not (project_dir / ".git").exists():
        logger.info("No .git found in %s, initializing git repo", project_dir)
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True, timeout=60)
        subprocess.run(["git", "add", "-A"], cwd=project_dir, capture_output=True, timeout=60)
        commit_proc = subprocess.run(
            [
                "git",
                "-c",
                "user.name=crs-claude-code",
                "-c",
                "user.email=crs-claude-code@local",
                "commit",
                "-m",
                "initial source",
            ],
            cwd=project_dir, capture_output=True, timeout=60,
        )
        if commit_proc.returncode != 0:
            stderr = (
                commit_proc.stderr.decode(errors="replace")
                if isinstance(commit_proc.stderr, bytes)
                else str(commit_proc.stderr)
            )
            logger.error("Failed to create initial commit: %s", stderr.strip())
            return None

    return project_dir


def wait_for_builder() -> bool:
    """Fail-fast DNS check for the builder sidecar."""
    try:
        domain = crs.get_service_domain(BUILDER_MODULE)
        logger.info("Builder sidecar '%s' resolved to %s", BUILDER_MODULE, domain)
        return True
    except RuntimeError as e:
        logger.error("Failed to resolve builder domain for '%s': %s", BUILDER_MODULE, e)
        return False


def load_agent(agent_name: str):
    """Dynamically load an agent module from the agents package."""
    module_name = f"agents.{agent_name}"
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        logger.error("Failed to load agent '%s': %s", agent_name, e)
        sys.exit(1)


def process_inputs(
    pov_paths: list[Path],
    source_dir: Path,
    agent,
    bug_candidate_paths: list[Path],
    ref_diff: str | None = None,
) -> bool:
    """Process available inputs in a single agent session."""
    try:
        _reset_source(source_dir)
    except Exception as e:
        logger.error("Failed to reset source before agent run: %s", e)
        return False

    agent_work_dir = WORK_DIR / "agent"
    agent_work_dir.mkdir(parents=True, exist_ok=True)

    existing_patches = _snapshot_patch_state()
    run_result = False

    run_sig = inspect.signature(agent.run)
    if "bug_candidates" in run_sig.parameters:
        run_kwargs = {
            "source_dir": source_dir,
            "povs": pov_paths,
            "bug_candidates": bug_candidate_paths,
            "harness": HARNESS,
            "patches_dir": PATCHES_DIR,
            "work_dir": agent_work_dir,
        }
        optional_kwargs = {
            "language": LANGUAGE,
            "sanitizer": SANITIZER,
            "builder": BUILDER_MODULE,
            "ref_diff": ref_diff,
        }
        for key, value in optional_kwargs.items():
            if key in run_sig.parameters:
                run_kwargs[key] = value
        run_result = bool(agent.run(**run_kwargs))
    else:
        old_kwargs = {}
        if "language" in run_sig.parameters:
            old_kwargs["language"] = LANGUAGE
        if "sanitizer" in run_sig.parameters:
            old_kwargs["sanitizer"] = SANITIZER
        if "builder" in run_sig.parameters:
            old_kwargs["builder"] = BUILDER_MODULE
        if "ref_diff" in run_sig.parameters:
            old_kwargs["ref_diff"] = ref_diff
        run_result = bool(
            agent.run(
                source_dir,
                pov_paths,
                HARNESS,
                PATCHES_DIR,
                agent_work_dir,
                **old_kwargs,
            )
        )

    post_run_reset_ok = True
    try:
        _reset_source(source_dir)
    except Exception as e:
        post_run_reset_ok = False
        logger.error("Failed to reset source after agent run: %s", e)

    current_patches = _snapshot_patch_state()
    changed_patch_names = sorted(
        name
        for name, state in current_patches.items()
        if existing_patches.get(name) != state
    )
    if changed_patch_names:
        if len(changed_patch_names) > 1:
            logger.warning(
                "Multiple changed patch files detected (%d): %s. Each file in %s is auto-submitted.",
                len(changed_patch_names), changed_patch_names, PATCHES_DIR,
            )
        logger.warning(
            "Submission is final: detected patch file(s) %s in %s. Submitted patches cannot be edited or resubmitted.",
            changed_patch_names, PATCHES_DIR,
        )
        logger.info("Updated/new patch produced: %s", changed_patch_names)
        return True

    if run_result:
        logger.warning(
            "Agent reported success but no new patch file was created in %s",
            PATCHES_DIR,
        )
    if not post_run_reset_ok:
        logger.warning("Source reset failed after agent run and no patch was produced")
    logger.warning("Agent did not produce a patch")
    return False


def main():
    logger.info(
        "Starting patcher: target=%s harness=%s agent=%s snapshot=%s",
        TARGET, HARNESS, CRS_AGENT, SNAPSHOT_IMAGE or "(none)",
    )

    if not SNAPSHOT_IMAGE:
        logger.error("OSS_CRS_SNAPSHOT_IMAGE is not set.")
        logger.error("Declare snapshot: true in target_build_phase and run_snapshot: true in crs_run_phase (crs.yaml).")
        sys.exit(1)

    global crs
    crs = init_crs_utils()

    PATCHES_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(
        target=crs.register_submit_dir,
        args=(DataType.PATCH, PATCHES_DIR),
        daemon=True,
    ).start()
    logger.info("Patch submission watcher started")

    pov_files_fetched = crs.fetch(DataType.POV, POV_DIR)
    logger.info("Fetched %d POV file(s) into %s", len(pov_files_fetched), POV_DIR)

    try:
        diff_files_fetched = crs.fetch(DataType.DIFF, DIFF_DIR)
        if diff_files_fetched:
            logger.info("Fetched %d diff file(s) into %s", len(diff_files_fetched), DIFF_DIR)
    except Exception as e:
        logger.warning("Diff fetch failed: %s — delta mode diffs unavailable", e)

    try:
        bug_files_fetched = crs.fetch(DataType.BUG_CANDIDATE, BUG_CANDIDATE_DIR)
        if bug_files_fetched:
            logger.info(
                "Fetched %d bug-candidate file(s) into %s",
                len(bug_files_fetched),
                BUG_CANDIDATE_DIR,
            )
    except Exception as e:
        logger.warning("Bug-candidate fetch failed: %s — static findings unavailable", e)

    claude_home = Path.home() / ".claude"
    claude_home_backup = claude_home.with_name(".claude.pre-crs-backup")
    had_existing_claude_home = claude_home.exists() or claude_home.is_symlink()
    if claude_home_backup.exists() or claude_home_backup.is_symlink():
        rotated_backup = claude_home_backup.with_name(f"{claude_home_backup.name}-{int(time.time())}")
        claude_home_backup.rename(rotated_backup)
    if had_existing_claude_home:
        claude_home.rename(claude_home_backup)

    try:
        crs.register_shared_dir(claude_home, "claude-logs")
        logger.info("Claude logs shared at %s", claude_home)
        if claude_home_backup.exists() or claude_home_backup.is_symlink():
            logger.info("Preserved previous Claude home backup at %s", claude_home_backup)
    except Exception as e:
        logger.warning("Failed to register claude-logs shared dir: %s", e)
        if claude_home.exists() or claude_home.is_symlink():
            if claude_home.is_symlink() or claude_home.is_file():
                claude_home.unlink()
            else:
                shutil.rmtree(claude_home)
        if claude_home_backup.exists() or claude_home_backup.is_symlink():
            claude_home_backup.rename(claude_home)
        claude_home.mkdir(parents=True, exist_ok=True)

    source_dir = setup_source()
    if source_dir is None:
        logger.error("Failed to set up source directory")
        sys.exit(1)

    logger.info("Source directory: %s", source_dir)

    agent = load_agent(CRS_AGENT)
    agent.setup(source_dir, {
        "llm_api_url": LLM_API_URL,
        "llm_api_key": LLM_API_KEY,
    })

    pov_files = sorted(f for f in POV_DIR.rglob("*") if f.is_file() and not f.name.startswith("."))
    bug_candidate_files = sorted(
        f for f in BUG_CANDIDATE_DIR.rglob("*") if f.is_file() and not f.name.startswith(".")
    )

    ref_diff_path = DIFF_DIR / "ref.diff"
    has_ref_diff = DIFF_DIR.exists() and ref_diff_path.is_file()

    if not pov_files and not bug_candidate_files and not has_ref_diff:
        logger.warning("No POV, bug-candidate, or ref.diff inputs found in %s, %s, and %s", POV_DIR, BUG_CANDIDATE_DIR, ref_diff_path)
        sys.exit(0)

    if pov_files:
        logger.info("Found %d POV(s): %s", len(pov_files), [p.name for p in pov_files])
    if bug_candidate_files:
        logger.info(
            "Found %d bug-candidate file(s): %s",
            len(bug_candidate_files),
            [p.name for p in bug_candidate_files],
        )

    ref_diff = None
    if has_ref_diff:
        ref_diff = ref_diff_path.read_text()
        logger.info("Reference diff found (%d chars)", len(ref_diff))

    if not wait_for_builder():
        logger.warning(
            "Builder sidecar DNS check failed at startup; continuing and relying on libCRS command-level retries/health waits"
        )

    if process_inputs(pov_files, source_dir, agent, bug_candidate_files, ref_diff=ref_diff):
        logger.info("Patch submitted. Waiting for daemon to flush...")
        time.sleep(SUBMISSION_FLUSH_WAIT_SECS)


if __name__ == "__main__":
    main()
