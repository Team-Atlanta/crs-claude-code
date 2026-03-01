"""
Template agent module.

Copy this file to create a new agent. Implement setup() and run()
following the interface below, then set CRS_AGENT=<your_module_name>.
"""

from pathlib import Path


def setup(source_dir: Path, config: dict) -> None:
    """One-time agent configuration.

    Called once at startup with the source directory and an agent-specific
    config dict (for example: API URL/key, model token, or agent home dir).
    """
    raise NotImplementedError("Implement setup() for your agent")


def run(
    source_dir: Path,
    povs: list[Path],
    bug_candidates: list[Path],
    harness: str,
    patches_dir: Path,
    work_dir: Path,
    *,
    language: str = "c",
    sanitizer: str = "address",
    builder: str,
    ref_diff: str | None = None,
) -> bool:
    """Run the agent autonomously.

    povs is a list of POV file paths — can be empty.
    bug_candidates is a list of bug-candidate report files (SARIF/JSON/text) — can be empty.

    The agent should:
    1. Analyze available evidence (reproduce POVs and/or inspect bug-candidate reports)
    2. Edit source files to fix the vulnerability
    3. Build and test using libCRS commands (pass --builder to each)
    4. Write verified .diff file(s) to patches_dir
    5. Verify the patch against all available validation signals

    Returns True if the agent believes it produced a patch.
    The orchestrator still treats actual `.diff` artifacts in patches_dir as authoritative.
    """
    raise NotImplementedError("Implement run() for your agent")
