"""
crs-claude-code patcher module.

Thin launcher that delegates vulnerability fixing to a swappable AI agent.
The agent (selected via CRS_AGENT env var) handles: bug analysis, code editing,
building (via libCRS), testing (via libCRS), iteration, and final patch
submission (writing .diff to /patches/).

To add a new agent, create a module in agents/ implementing setup() and run().
"""

import hashlib
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

TARGET = os.environ.get("OSS_CRS_TARGET", "")
HARNESS = os.environ.get("OSS_CRS_TARGET_HARNESS", "")
LANGUAGE = os.environ.get("FUZZING_LANGUAGE", "c")
SANITIZER = os.environ.get("SANITIZER", "address")
LLM_API_URL = os.environ.get("OSS_CRS_LLM_API_URL", "")
LLM_API_KEY = os.environ.get("OSS_CRS_LLM_API_KEY", "")

CRS_AGENT = os.environ.get("CRS_AGENT", "claude_code")

WORK_DIR = Path("/work")
SRC_DIR = Path("/src")
PATCHES_DIR = Path("/patches")
POV_DIR = WORK_DIR / "povs"
DIFF_DIR = WORK_DIR / "diffs"
BUG_CANDIDATE_DIR = WORK_DIR / "bug-candidates"
SEED_DIR = WORK_DIR / "seeds"
PATCH_POLL_INTERVAL_SECS = 0.5
PATCH_STABLE_POLLS = 3
PATCH_FALLBACK_WAIT_SECS = 2.0

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


def _snapshot_patch_state(patches_dir: Path) -> dict[str, tuple[int, int]]:
    """Capture patch file state by name -> (mtime_ns, size)."""
    state: dict[str, tuple[int, int]] = {}
    for p in patches_dir.glob("*.diff"):
        try:
            st = p.stat()
        except OSError:
            continue
        state[p.name] = (st.st_mtime_ns, st.st_size)
    return state


def _changed_patches(
    before: dict[str, tuple[int, int]],
    patches_dir: Path,
) -> list[str]:
    """Return sorted patch names that are new or modified since snapshot."""
    now = _snapshot_patch_state(patches_dir)
    return sorted(name for name, state in now.items() if before.get(name) != state)


def _is_patch_candidate(path: Path) -> bool:
    """Return True when the path looks like a patch artifact candidate."""
    if not path.is_file() or path.name.startswith(".") or path.suffix != ".diff":
        return False
    try:
        path.stat()
        return True
    except OSError:
        return False


def _read_patch_signature(path: Path) -> tuple[int, int, str] | None:
    """Return a stable signature for a patch file or None if still in flux."""
    if not _is_patch_candidate(path):
        return None
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except OSError:
        return None
    if (
        before.st_mtime_ns != after.st_mtime_ns
        or before.st_size != after.st_size
        or after.st_size == 0
        or not data.strip()
    ):
        return None
    digest = hashlib.blake2b(data, digest_size=16).hexdigest()
    return after.st_mtime_ns, after.st_size, digest


def _observe_first_patch(
    before: dict[str, tuple[int, int]],
    first_patch_name_ref: dict[str, str | None],
) -> Path | None:
    """Latch and return the first changed patch candidate observed this run."""
    first_patch_name = first_patch_name_ref.get("name")
    if first_patch_name:
        path = PATCHES_DIR / first_patch_name
        if _is_patch_candidate(path):
            return path
        first_patch_name_ref["name"] = None
    for name in _changed_patches(before, PATCHES_DIR):
        path = PATCHES_DIR / name
        if not _is_patch_candidate(path):
            continue
        first_patch_name_ref["name"] = name
        return path
    return None


def _submit_patch_once(
    patch_path: Path,
    submission_state: dict[str, bool],
    submission_lock: threading.Lock,
    *,
    exit_after_submit: bool,
) -> bool:
    """Submit the selected patch at most once."""
    with submission_lock:
        if submission_state["attempted"]:
            return submission_state["succeeded"]
        submission_state["attempted"] = True
    logger.warning("Submission is final: submitting first patch %s", patch_path)
    try:
        crs.submit(DataType.PATCH, patch_path)
    except Exception:
        logger.exception("Failed to submit patch %s", patch_path)
        if exit_after_submit:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(1)
        raise RuntimeError(f"failed to submit patch: {patch_path}") from None
    with submission_lock:
        submission_state["succeeded"] = True
    if exit_after_submit:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return True


def _watch_for_first_patch(
    existing_patches: dict[str, tuple[int, int]],
    stop_event: threading.Event,
    first_patch_name_ref: dict[str, str | None],
    submission_state: dict[str, bool],
    submission_lock: threading.Lock,
) -> None:
    """Submit the first stable patch file observed in /patches and exit."""
    last_signature: tuple[int, int, str] | None = None
    stable_polls = 0
    while not stop_event.is_set():
        candidate_path = _observe_first_patch(existing_patches, first_patch_name_ref)
        if candidate_path is None:
            time.sleep(PATCH_POLL_INTERVAL_SECS)
            continue
        signature = _read_patch_signature(candidate_path)
        if signature is None:
            last_signature = None
            stable_polls = 0
            time.sleep(PATCH_POLL_INTERVAL_SECS)
            continue
        if signature == last_signature:
            stable_polls += 1
        else:
            last_signature = signature
            stable_polls = 1
        if stable_polls >= PATCH_STABLE_POLLS:
            _submit_patch_once(
                candidate_path,
                submission_state,
                submission_lock,
                exit_after_submit=True,
            )
        time.sleep(PATCH_POLL_INTERVAL_SECS)


def _wait_for_stable_first_patch(
    existing_patches: dict[str, tuple[int, int]],
    first_patch_name_ref: dict[str, str | None],
    timeout_secs: float,
) -> Path | None:
    """Wait briefly for the first observed patch to settle before fallback submit."""
    deadline = time.monotonic() + timeout_secs
    last_signature: tuple[int, int, str] | None = None
    stable_polls = 0
    while time.monotonic() < deadline:
        candidate_path = _observe_first_patch(existing_patches, first_patch_name_ref)
        if candidate_path is None:
            time.sleep(PATCH_POLL_INTERVAL_SECS)
            continue
        signature = _read_patch_signature(candidate_path)
        if signature is None:
            last_signature = None
            stable_polls = 0
            time.sleep(PATCH_POLL_INTERVAL_SECS)
            continue
        if signature == last_signature:
            stable_polls += 1
        else:
            last_signature = signature
            stable_polls = 1
        if stable_polls >= PATCH_STABLE_POLLS:
            return candidate_path
        time.sleep(PATCH_POLL_INTERVAL_SECS)
    return None


def setup_source() -> Path | None:
    """Download build-output /src and prepare it as the working directory."""
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

    try:
        crs.download_build_output("src", SRC_DIR)
    except Exception as e:
        logger.error("Failed to download /src build output via libCRS: %s", e)
        return None

    worktree_dir = SRC_DIR.resolve()

    if (worktree_dir / ".git").exists():
        return worktree_dir

    logger.info("No .git found in %s, initializing git repo", worktree_dir)
    subprocess.run(["git", "init"], cwd=worktree_dir, capture_output=True, timeout=60)
    subprocess.run(["git", "add", "-A"], cwd=worktree_dir, capture_output=True, timeout=60)
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
        cwd=worktree_dir, capture_output=True, timeout=60,
    )
    if commit_proc.returncode != 0:
        stderr = (
            commit_proc.stderr.decode(errors="replace")
            if isinstance(commit_proc.stderr, bytes)
            else str(commit_proc.stderr)
        )
        logger.error("Failed to create initial commit: %s", stderr.strip())
        return None

    return worktree_dir


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
    diff_paths: list[Path],
    seed_paths: list[Path],
    source_dir: Path,
    agent,
    bug_candidate_paths: list[Path],
) -> bool:
    """Process available inputs in a single agent session."""
    try:
        _reset_source(source_dir)
    except Exception as e:
        logger.error("Failed to reset source before agent run: %s", e)
        return False

    agent_work_dir = WORK_DIR / "agent"
    agent_work_dir.mkdir(parents=True, exist_ok=True)

    existing_patches = _snapshot_patch_state(PATCHES_DIR)
    first_patch_name_ref: dict[str, str | None] = {"name": None}
    submission_state = {"attempted": False, "succeeded": False}
    submission_lock = threading.Lock()
    submit_stop_event = threading.Event()
    submit_thread = threading.Thread(
        target=_watch_for_first_patch,
        args=(
            existing_patches,
            submit_stop_event,
            first_patch_name_ref,
            submission_state,
            submission_lock,
        ),
        daemon=True,
    )
    submit_thread.start()
    run_result = False

    run_sig = inspect.signature(agent.run)
    required_params = {"pov_dir", "bug_candidate_dir", "diff_dir", "seed_dir"}
    missing_params = sorted(param for param in required_params if param not in run_sig.parameters)
    if missing_params:
        raise TypeError(
            f"Agent.run must accept directory-based inputs {sorted(required_params)}; "
            f"missing: {missing_params}"
        )
    run_kwargs = {
        "source_dir": source_dir,
        "pov_dir": POV_DIR,
        "bug_candidate_dir": BUG_CANDIDATE_DIR,
        "diff_dir": DIFF_DIR,
        "seed_dir": SEED_DIR,
        "harness": HARNESS,
        "patches_dir": PATCHES_DIR,
        "work_dir": agent_work_dir,
    }
    optional_kwargs = {
        "language": LANGUAGE,
        "sanitizer": SANITIZER,
    }
    for key, value in optional_kwargs.items():
        if key in run_sig.parameters:
            run_kwargs[key] = value
    run_result = bool(agent.run(**run_kwargs))

    submit_stop_event.set()
    submit_thread.join(timeout=1)

    post_run_reset_ok = True
    try:
        _reset_source(source_dir)
    except Exception as e:
        post_run_reset_ok = False
        logger.error("Failed to reset source after agent run: %s", e)

    selected_patch = None
    with submission_lock:
        submission_attempted = submission_state["attempted"]
    if not submission_attempted:
        selected_patch = _wait_for_stable_first_patch(
            existing_patches,
            first_patch_name_ref,
            PATCH_FALLBACK_WAIT_SECS,
        )
    if selected_patch is not None:
        logger.warning("Agent produced patch %s after watcher shutdown; submitting now", selected_patch)
        return _submit_patch_once(
            selected_patch,
            submission_state,
            submission_lock,
            exit_after_submit=False,
        )

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
        "Starting patcher: target=%s harness=%s agent=%s",
        TARGET, HARNESS, CRS_AGENT,
    )

    global crs
    crs = init_crs_utils()

    PATCHES_DIR.mkdir(parents=True, exist_ok=True)

    try:
        pov_files_fetched = crs.fetch(DataType.POV, POV_DIR)
        if pov_files_fetched:
            logger.info("Fetched %d POV file(s) into %s", len(pov_files_fetched), POV_DIR)
    except Exception as e:
        logger.info("No POV input fetched: %s", e)

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

    try:
        seed_files_fetched = crs.fetch(DataType.SEED, SEED_DIR)
        if seed_files_fetched:
            logger.info("Fetched %d seed file(s) into %s", len(seed_files_fetched), SEED_DIR)
    except Exception as e:
        logger.warning("Seed fetch failed: %s — seeds unavailable", e)

    # Register Claude home as a log directory for post-run analysis.
    # register-log-dir creates a symlink, so the path must not exist beforehand.
    # Preserve existing Claude home and restore it if registration fails.
    claude_home = Path.home() / ".claude"
    claude_home_backup = claude_home.with_name(".claude.pre-crs-backup")
    had_existing_claude_home = claude_home.exists() or claude_home.is_symlink()
    if claude_home_backup.exists() or claude_home_backup.is_symlink():
        rotated_backup = claude_home_backup.with_name(f"{claude_home_backup.name}-{int(time.time())}")
        claude_home_backup.rename(rotated_backup)
    if had_existing_claude_home:
        claude_home.rename(claude_home_backup)

    try:
        crs.register_log_dir(claude_home)
        logger.info("Claude home registered as log dir at %s", claude_home)
        if claude_home_backup.exists() or claude_home_backup.is_symlink():
            logger.info("Preserved previous Claude home backup at %s", claude_home_backup)
    except Exception as e:
        logger.warning("Failed to register claude-home log dir: %s", e)
        if claude_home.exists() or claude_home.is_symlink():
            if claude_home.is_symlink() or claude_home.is_file():
                claude_home.unlink()
            else:
                shutil.rmtree(claude_home)
        if claude_home_backup.exists() or claude_home_backup.is_symlink():
            claude_home_backup.rename(claude_home)
            logger.info("Restored previous Claude home from backup")
        else:
            claude_home.mkdir(parents=True, exist_ok=True)

    # Register agent work directory as a log dir so stdout/stderr and
    # libCRS response directories are persisted for post-run analysis.
    agent_work_dir = WORK_DIR / "agent"
    try:
        crs.register_log_dir(agent_work_dir)
        logger.info("Agent work dir registered as log dir at %s", agent_work_dir)
    except Exception as e:
        logger.warning("Failed to register agent work log dir: %s", e)

    worktree_dir = setup_source()
    if worktree_dir is None:
        logger.error("Failed to set up source directory")
        sys.exit(1)

    logger.info("Worktree directory: %s", worktree_dir)

    agent = load_agent(CRS_AGENT)
    agent.setup(worktree_dir, {
        "llm_api_url": LLM_API_URL,
        "llm_api_key": LLM_API_KEY,
        "claude_home": str(claude_home),
    })

    pov_files = sorted(f for f in POV_DIR.rglob("*") if f.is_file() and not f.name.startswith("."))
    bug_candidate_files = sorted(
        f for f in BUG_CANDIDATE_DIR.rglob("*") if f.is_file() and not f.name.startswith(".")
    )
    diff_files = sorted(f for f in DIFF_DIR.rglob("*") if f.is_file() and not f.name.startswith("."))
    diff_files = [f for f in diff_files if f.read_text(errors="replace").strip()]
    seed_files = sorted(f for f in SEED_DIR.rglob("*") if f.is_file() and not f.name.startswith("."))

    if not pov_files and not bug_candidate_files and not diff_files and not seed_files:
        logger.warning(
            "No startup inputs found in %s, %s, %s, or %s",
            POV_DIR,
            BUG_CANDIDATE_DIR,
            DIFF_DIR,
            SEED_DIR,
        )
        sys.exit(0)

    if pov_files:
        logger.info("Found %d POV(s): %s", len(pov_files), [p.name for p in pov_files])
    if bug_candidate_files:
        logger.info(
            "Found %d bug-candidate file(s): %s",
            len(bug_candidate_files),
            [p.name for p in bug_candidate_files],
        )
    if diff_files:
        logger.info("Found %d diff file(s): %s", len(diff_files), [p.name for p in diff_files])
    if seed_files:
        logger.info("Found %d seed file(s): %s", len(seed_files), [p.name for p in seed_files])

    if process_inputs(pov_files, diff_files, seed_files, worktree_dir, agent, bug_candidate_files):
        logger.info("Patch submitted")


if __name__ == "__main__":
    main()
