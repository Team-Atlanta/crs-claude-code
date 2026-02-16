"""
Template agent module.

Copy this file to create a new agent. Implement setup() and run()
following the interface below, then set CRS_AGENT=<your_module_name>.
"""

from pathlib import Path


def setup(source_dir: Path, config: dict) -> None:
    """One-time agent configuration.

    Called once at startup with the source directory and a config dict
    containing at least: llm_api_url, llm_api_key.
    """
    raise NotImplementedError("Implement setup() for your agent")


def run(
    source_dir: Path,
    crash_log: str,
    pov_path: Path,
    harness: str,
    patches_dir: Path,
    work_dir: Path,
    language: str = "c",
) -> bool:
    """Run the agent autonomously.

    The agent should:
    1. Analyze the crash log
    2. Edit source files to fix the vulnerability
    3. Build and test using libCRS commands
    4. Write verified .diff file(s) to patches_dir

    Returns True if the agent produced a patch.
    """
    raise NotImplementedError("Implement run() for your agent")
