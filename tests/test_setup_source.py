from pathlib import Path
from types import SimpleNamespace

import patcher


def test_setup_source_uses_repo_hint_instead_of_scanning_nested_git_repos(
    monkeypatch, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    source_dir = work_dir / "src"
    project_dir = source_dir / "mock-c"
    other_repo_dir = source_dir / "vendored"
    (project_dir / ".git").mkdir(parents=True)
    (other_repo_dir / ".git").mkdir(parents=True)

    monkeypatch.setattr(patcher, "WORK_DIR", work_dir)
    monkeypatch.setattr(
        patcher,
        "crs",
        SimpleNamespace(download_source=lambda source_type, dst: project_dir),
    )

    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(cmd, cwd=None, capture_output=None, timeout=None):
        calls.append((list(cmd), cwd))
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(patcher.subprocess, "run", fake_run)

    resolved = patcher.setup_source()

    assert resolved == project_dir
    assert ["git", "init"] not in [cmd for cmd, _ in calls]


def test_setup_source_initializes_git_only_in_returned_project_dir(
    monkeypatch, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    source_dir = work_dir / "src"
    project_dir = source_dir / "mock-c"
    project_dir.mkdir(parents=True)

    monkeypatch.setattr(patcher, "WORK_DIR", work_dir)
    monkeypatch.setattr(
        patcher,
        "crs",
        SimpleNamespace(download_source=lambda source_type, dst: project_dir),
    )

    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(cmd, cwd=None, capture_output=None, timeout=None):
        calls.append((list(cmd), cwd))
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(patcher.subprocess, "run", fake_run)

    resolved = patcher.setup_source()

    assert resolved == project_dir
    assert (["git", "init"], project_dir) in calls
    assert (["git", "init"], source_dir) not in calls
