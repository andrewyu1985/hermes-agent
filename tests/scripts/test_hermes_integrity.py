"""Behavior contracts for the read-only Codex Git-HEAD drift detector."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.hermes_integrity import TRACKED_CODEX_SOURCES, check_integrity
from scripts import hermes_integrity


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )


def _repo(tmp_path: Path, *, tracked: tuple[str, ...] = TRACKED_CODEX_SOURCES) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Hermes Test")
    _git(repo, "config", "user.email", "hermes@example.invalid")
    for index, relative in enumerate(tracked):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"VALUE_{index} = {index}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo


def test_clean_committed_sources_pass(tmp_path: Path) -> None:
    assert check_integrity(_repo(tmp_path)) == []


def test_modified_source_reports_drift_without_mutation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = repo / TRACKED_CODEX_SOURCES[0]
    source.write_bytes(b"VALUE = 'locally changed'\n")
    before = source.read_bytes()

    problems = check_integrity(repo)

    assert f"{TRACKED_CODEX_SOURCES[0]}: differs from HEAD" in problems
    assert source.read_bytes() == before


def test_missing_source_is_reported_without_restore(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = repo / TRACKED_CODEX_SOURCES[1]
    source.unlink()

    problems = check_integrity(repo)

    assert f"{TRACKED_CODEX_SOURCES[1]}: missing from worktree" in problems
    assert not source.exists()


def test_invalid_python_is_reported_alongside_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = repo / TRACKED_CODEX_SOURCES[2]
    source.write_bytes(b"def broken(:\n")

    problems = check_integrity(repo)

    assert any(problem.startswith(f"{TRACKED_CODEX_SOURCES[2]}: invalid Python syntax:") for problem in problems)
    assert f"{TRACKED_CODEX_SOURCES[2]}: differs from HEAD" in problems


def test_source_not_tracked_in_head_is_reported(tmp_path: Path) -> None:
    missing_from_head = TRACKED_CODEX_SOURCES[2]
    repo = _repo(tmp_path, tracked=TRACKED_CODEX_SOURCES[:2])
    source = repo / missing_from_head
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("UNTRACKED = True\n", encoding="utf-8")

    problems = check_integrity(repo)

    assert f"{missing_from_head}: not tracked in HEAD" in problems


def test_repository_without_head_reports_git_error(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    _git(repo, "init", "-q")
    for relative in TRACKED_CODEX_SOURCES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALID = True\n", encoding="utf-8")

    problems = check_integrity(repo)

    assert any(problem.startswith("git HEAD error:") for problem in problems)


def test_read_error_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = repo / TRACKED_CODEX_SOURCES[0]
    original = os.open

    def fail_target(path: Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if Path(path) == target:
            raise OSError("fixture read failure")
        if dir_fd is None:
            return original(path, flags, mode)
        return original(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_target)

    problems = check_integrity(repo)

    assert any(
        problem.startswith(f"{TRACKED_CODEX_SOURCES[0]}: read error: OSError:")
        for problem in problems
    )


@pytest.mark.platforms("posix")
def test_symlink_with_identical_bytes_is_rejected_without_being_followed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    relative = TRACKED_CODEX_SOURCES[0]
    source = repo / relative
    target = repo / "identical.py"
    target.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(target)

    problems = check_integrity(repo)

    assert f"{relative}: worktree path is a symlink" in problems


@pytest.mark.platforms("posix")
def test_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    relative = TRACKED_CODEX_SOURCES[1]
    source = repo / relative
    source.unlink()
    os.mkfifo(source)

    problems = check_integrity(repo)

    assert f"{relative}: worktree path is not a regular file (fifo)" in problems


@pytest.mark.platforms("posix")
def test_executable_mode_drift_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    relative = TRACKED_CODEX_SOURCES[2]
    source = repo / relative
    source.chmod(source.stat().st_mode | stat.S_IXUSR)

    problems = check_integrity(repo)

    assert f"{relative}: executable mode differs from HEAD" in problems


@pytest.mark.platforms("posix")
def test_non_regular_head_blob_mode_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    relative = TRACKED_CODEX_SOURCES[0]
    source = repo / relative
    source.unlink()
    source.symlink_to("target.py")
    _git(repo, "add", relative)
    _git(repo, "commit", "-qm", "symlink in HEAD")
    source.unlink()
    source.write_text("target.py", encoding="utf-8")

    problems = check_integrity(repo)

    assert f"{relative}: HEAD entry is not a regular file (mode 120000, type blob)" in problems


def test_one_captured_head_oid_is_used_for_every_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    for relative in TRACKED_CODEX_SOURCES:
        (repo / relative).write_text("CHANGED = True\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "moving HEAD")
    moved = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _git(repo, "reset", "--hard", baseline)

    original_git = hermes_integrity._git
    tree_refs: list[str] = []

    def move_head_after_capture(repo_arg: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        result = original_git(repo_arg, *args)
        if args == ("rev-parse", "--verify", "HEAD^{commit}"):
            _git(repo, "update-ref", "HEAD", moved)
        elif args and args[0] == "ls-tree":
            tree_refs.append(args[4])
        return result

    monkeypatch.setattr(hermes_integrity, "_git", move_head_after_capture)

    assert check_integrity(repo) == []
    assert tree_refs == [baseline] * len(TRACKED_CODEX_SOURCES)


def test_report_only_hook_always_exits_zero_on_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "scripts").mkdir()
    source_script = Path(__file__).resolve().parents[2] / "scripts" / "hermes_fix_integrity_check.sh"
    source_checker = Path(__file__).resolve().parents[2] / "scripts" / "hermes_integrity.py"
    (repo / "scripts" / source_script.name).write_bytes(source_script.read_bytes())
    (repo / "scripts" / source_checker.name).write_bytes(source_checker.read_bytes())
    (repo / TRACKED_CODEX_SOURCES[0]).write_text("DRIFT = True\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(repo / "scripts" / source_script.name)],
        cwd=tmp_path,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "manual review" in result.stderr


def test_watchdog_check_fixes_delegates_to_repo_integrity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from scripts import hermes_watchdog

    repo = _repo(tmp_path)
    changed = TRACKED_CODEX_SOURCES[0]
    (repo / changed).write_text("DRIFT = True\n", encoding="utf-8")
    monkeypatch.setattr(hermes_watchdog, "HERMES_DIR", repo)

    assert f"{changed}: differs from HEAD" in hermes_watchdog.check_fixes()
