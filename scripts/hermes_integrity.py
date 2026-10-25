#!/usr/bin/env python3
"""Report drift in vetted Codex sources against the current Git HEAD.

This checker is deliberately read-only and version-neutral: a vetted release
moves HEAD, and HEAD becomes the baseline. It does not import Hermes runtime
code, contact providers, or repair files.
"""
from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
from pathlib import Path

TRACKED_CODEX_SOURCES = (
    "agent/codex_runtime.py",
    "agent/auxiliary_client.py",
    "agent/transports/codex.py",
)


def _git_env() -> dict[str, str]:
    blocked = {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_DIR",
        "GIT_EXEC_PATH",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in blocked and not key.startswith("GIT_CONFIG_KEY_")
        and not key.startswith("GIT_CONFIG_VALUE_")
    }
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    command = [
        "git",
        "--no-pager",
        "--no-replace-objects",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        str(repo),
        *args,
    ]
    return subprocess.run(
        command,
        env=_git_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _detail(proc: subprocess.CompletedProcess[bytes]) -> str:
    text = proc.stderr.decode("utf-8", errors="replace").strip()
    return text or f"git exited {proc.returncode}"


def _head_blob(repo: Path, head_oid: str, relative: str) -> tuple[bytes | None, str | None, str | None]:
    listing = _git(repo, "ls-tree", "-r", "-z", "--full-tree", head_oid, "--", relative)
    if listing.returncode != 0:
        return None, None, f"{relative}: git error reading HEAD tree: {_detail(listing)}"
    if not listing.stdout:
        return None, None, f"{relative}: not tracked in HEAD"
    try:
        metadata, listed_path = listing.stdout[:-1].split(b"\t", 1)
        mode, object_type, _object_id = metadata.split(b" ", 2)
    except ValueError:
        return None, None, f"{relative}: git error reading HEAD tree: malformed ls-tree output"
    if listed_path.decode("utf-8", errors="surrogateescape") != relative:
        return None, None, f"{relative}: not a tracked file in HEAD"
    mode_text = mode.decode("ascii", errors="replace")
    type_text = object_type.decode("ascii", errors="replace")
    if object_type != b"blob" or mode not in {b"100644", b"100755"}:
        return (
            None,
            None,
            f"{relative}: HEAD entry is not a regular file (mode {mode_text}, type {type_text})",
        )
    blob = _git(repo, "cat-file", "blob", f"{head_oid}:{relative}")
    if blob.returncode != 0:
        return None, None, f"{relative}: git error reading HEAD blob: {_detail(blob)}"
    return blob.stdout, mode_text, None


def _file_type(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISCHR(mode):
        return "character device"
    if stat.S_ISBLK(mode):
        return "block device"
    if stat.S_ISSOCK(mode):
        return "socket"
    return "unknown"


def _worktree_file(path: Path, relative: str) -> tuple[bytes | None, os.stat_result | None, str | None]:
    try:
        initial = os.lstat(path)
    except FileNotFoundError:
        return None, None, f"{relative}: missing from worktree"
    except OSError as exc:
        return None, None, f"{relative}: read error: {type(exc).__name__}: {exc}"
    if stat.S_ISLNK(initial.st_mode):
        return None, None, f"{relative}: worktree path is a symlink"
    if not stat.S_ISREG(initial.st_mode):
        return None, None, f"{relative}: worktree path is not a regular file ({_file_type(initial.st_mode)})"

    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_NONBLOCK", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                return None, None, f"{relative}: worktree path changed to a non-regular file"
            if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
                return None, None, f"{relative}: worktree path changed while being inspected"
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                return handle.read(), opened, None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except OSError as exc:
        return None, None, f"{relative}: read error: {type(exc).__name__}: {exc}"


def check_integrity(repo: Path) -> list[str]:
    """Return read-only drift and syntax diagnostics for vetted Codex files."""
    repo = Path(repo)
    problems: list[str] = []
    try:
        head = _git(repo, "rev-parse", "--verify", "HEAD^{commit}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"git HEAD error: {type(exc).__name__}: {exc}"]
    if head.returncode != 0:
        return [f"git HEAD error: {_detail(head)}"]
    try:
        head_oid = head.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        return ["git HEAD error: malformed commit OID"]

    for relative in TRACKED_CODEX_SOURCES:
        path = repo / relative
        current, current_stat, worktree_problem = _worktree_file(path, relative)
        if worktree_problem:
            problems.append(worktree_problem)

        if current is not None:
            try:
                ast.parse(current, filename=relative)
            except (SyntaxError, ValueError) as exc:
                location = f" line {exc.lineno}" if isinstance(exc, SyntaxError) and exc.lineno else ""
                problems.append(f"{relative}: invalid Python syntax:{location}: {exc.msg if isinstance(exc, SyntaxError) else exc}")

        try:
            expected, head_mode, git_problem = _head_blob(repo, head_oid, relative)
        except (OSError, subprocess.TimeoutExpired) as exc:
            expected = None
            head_mode = None
            git_problem = f"{relative}: git error: {type(exc).__name__}: {exc}"
        if git_problem:
            problems.append(git_problem)
        elif current is not None:
            if current != expected:
                problems.append(f"{relative}: differs from HEAD")
            if os.name == "posix" and current_stat is not None:
                worktree_executable = bool(current_stat.st_mode & 0o111)
                if worktree_executable != (head_mode == "100755"):
                    problems.append(f"{relative}: executable mode differs from HEAD")

    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    repo = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    problems = check_integrity(repo)
    for problem in problems:
        print(f"WARN: {problem}", file=sys.stderr)
    if not problems:
        print("OK: vetted Codex sources match Git HEAD and parse successfully")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
