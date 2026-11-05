import json
import runpy
import subprocess
from pathlib import Path

import pytest


def test_restore_state_uses_current_branch_and_daily_workflow(monkeypatch, tmp_path):
    restore = runpy.run_path("scripts/restore_state.py")["main"]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_REPO", "owner/pulse")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    artifacts = [
        {"expired": True, "workflow_run": {"id": 4, "head_branch": "main"}},
        {"expired": False, "workflow_run": {"id": 3, "head_branch": "other"}},
        {"expired": False, "workflow_run": {"id": 2, "head_branch": "main"}},
        {"expired": False, "workflow_run": {"id": 1, "head_branch": "main"}},
    ]
    calls = []

    def output(command, **kwargs):
        calls.append(command)
        if "--slurp" in command:
            return json.dumps([{"artifacts": artifacts}])
        return ".github/workflows/ci.yml" if command[2].endswith("/2") else ".github/workflows/digest.yml"

    def download(command, **kwargs):
        calls.append(command)
        Path("data").mkdir()
        Path("data/pulse.db").write_bytes(b"recorded state")

    monkeypatch.setattr(subprocess, "check_output", output)
    monkeypatch.setattr(subprocess, "run", download)
    restore()
    assert calls[-1][1:4] == ["run", "download", "1"]
    assert Path("data/pulse.db").read_bytes() == b"recorded state"


def test_missing_state_is_visible_and_does_not_fabricate_database(monkeypatch, tmp_path, capsys):
    restore = runpy.run_path("scripts/restore_state.py")["main"]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_REPO", "owner/pulse")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: '[{"artifacts":[]}]')
    restore()
    assert "未找到" in capsys.readouterr().out
    assert not Path("data/pulse.db").exists()


def test_github_failure_does_not_fall_back_to_empty_state(monkeypatch, tmp_path):
    restore = runpy.run_path("scripts/restore_state.py")["main"]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_REPO", "owner/pulse")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["gh", "api"])

    monkeypatch.setattr(subprocess, "check_output", fail)
    with pytest.raises(subprocess.CalledProcessError):
        restore()
    assert not Path("data/pulse.db").exists()
