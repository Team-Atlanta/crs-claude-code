from pathlib import Path
from types import SimpleNamespace

import patcher


def test_setup_source_returns_download_root_when_git_exists(
    monkeypatch, tmp_path: Path
) -> None:
    """With mount-based API, worktree_dir is always download_root (src/)."""
    work_dir = tmp_path / "work"
    source_dir = work_dir / "src"
    (source_dir / ".git").mkdir(parents=True)

    monkeypatch.setattr(patcher, "WORK_DIR", work_dir)
    monkeypatch.setattr(
        patcher,
        "crs",
        SimpleNamespace(download_source=lambda source_type, dst: None),
    )

    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(cmd, cwd=None, capture_output=None, timeout=None):
        calls.append((list(cmd), cwd))
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(patcher.subprocess, "run", fake_run)

    resolved = patcher.setup_source()

    assert resolved == source_dir.resolve()
    assert ["git", "init"] not in [cmd for cmd, _ in calls]


def test_setup_source_initializes_git_when_no_dotgit(
    monkeypatch, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    source_dir = work_dir / "src"
    source_dir.mkdir(parents=True)

    monkeypatch.setattr(patcher, "WORK_DIR", work_dir)
    monkeypatch.setattr(
        patcher,
        "crs",
        SimpleNamespace(download_source=lambda source_type, dst: None),
    )

    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(cmd, cwd=None, capture_output=None, timeout=None):
        calls.append((list(cmd), cwd))
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(patcher.subprocess, "run", fake_run)

    resolved = patcher.setup_source()

    assert resolved == source_dir.resolve()
    assert (["git", "init"], source_dir.resolve()) in calls
