from pathlib import Path
from types import SimpleNamespace

from agents import claude_code


def test_run_does_not_pass_debug_file_flag(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    pov_dir = tmp_path / "povs"
    pov_dir.mkdir()
    (pov_dir / "pov_0.blob").write_bytes(b"pov")
    bug_dir = tmp_path / "bugs"
    bug_dir.mkdir()
    diff_dir = tmp_path / "diffs"
    diff_dir.mkdir()
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    monkeypatch.setattr(
        claude_code,
        "_load_prompt_templates",
        lambda: {
            "agents_md": "{workflow_section}\n{pov_section}\n{bug_candidate_section}\n{seed_section}\n{pre_submit_section}\n{diff_section}",
            "workflow_pov": "workflow",
            "workflow_static": "workflow",
            "pov_present": "{pov_list}",
            "bug_candidates_present": "{bug_candidate_list}",
            "diff_present": "{diff_list}",
            "seed_present": "{seed_list}",
            "pre_submit": "{pov_line}{diff_line}",
        },
    )
    monkeypatch.setattr(claude_code, "AGENT_TIMEOUT", 0)
    monkeypatch.setattr(
        claude_code.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b""),
    )

    captured: dict[str, object] = {}

    class FakeStdin:
        def __init__(self) -> None:
            self.buffer = ""

        def write(self, data: str) -> None:
            self.buffer += data

        def close(self) -> None:
            return None

    class FakePopen:
        def __init__(self, cmd, stdin, stdout, stderr, text, cwd, start_new_session):
            captured["cmd"] = list(cmd)
            captured["cwd"] = cwd
            captured["stdin"] = FakeStdin()
            self.stdin = captured["stdin"]
            self.returncode = 0
            self.pid = 12345

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(claude_code.subprocess, "Popen", FakePopen)

    produced = claude_code.run(
        source_dir,
        pov_dir,
        bug_dir,
        diff_dir,
        seed_dir,
        "fuzz_parse_buffer_section",
        patches_dir,
        work_dir,
        builder="inc-builder",
    )

    assert produced is False
    cmd = captured["cmd"]
    assert cmd[:3] == ["claude", "-p", "--dangerously-skip-permissions"]
    assert "--append-system-prompt" in cmd
    assert "--debug-file" not in cmd
