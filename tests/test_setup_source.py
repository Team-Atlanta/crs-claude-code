from pathlib import Path
from types import SimpleNamespace

import patcher


def test_setup_source_returns_src_dir(
    monkeypatch, tmp_path: Path
) -> None:
    """setup_source downloads /src build output and returns it as worktree."""
    src_dir = tmp_path / "src"
    (src_dir / ".git").mkdir(parents=True)

    monkeypatch.setattr(patcher, "SRC_DIR", src_dir)
    monkeypatch.setattr(
        patcher,
        "crs",
        SimpleNamespace(download_build_output=lambda name, dst: None),
    )

    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(cmd, cwd=None, capture_output=None, timeout=None):
        calls.append((list(cmd), cwd))
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(patcher.subprocess, "run", fake_run)

    resolved = patcher.setup_source()

    assert resolved == src_dir.resolve()
    assert ["git", "init"] not in [cmd for cmd, _ in calls]


def test_setup_source_initializes_git_when_no_dotgit(
    monkeypatch, tmp_path: Path
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)

    monkeypatch.setattr(patcher, "SRC_DIR", src_dir)
    monkeypatch.setattr(
        patcher,
        "crs",
        SimpleNamespace(download_build_output=lambda name, dst: None),
    )

    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(cmd, cwd=None, capture_output=None, timeout=None):
        calls.append((list(cmd), cwd))
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(patcher.subprocess, "run", fake_run)

    resolved = patcher.setup_source()

    assert resolved == src_dir.resolve()
    assert (["git", "init"], src_dir.resolve()) in calls
