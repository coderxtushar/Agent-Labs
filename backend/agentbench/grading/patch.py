"""Workspace diffing.

The patch is computed on the host by comparing a pristine snapshot of the
repository against the workspace the agent left behind.  Doing it this way
rather than with ``git diff`` inside the sandbox means:

* no benchmark image is required to ship ``git``;
* new, deleted and renamed files are all captured uniformly;
* the diff is byte-deterministic for the same pair of trees, which matters when
  comparing runs.

The output is a standard unified diff with ``a/``/``b/`` prefixes, so it applies
with ``git apply`` or ``patch -p1``.
"""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

#: Build artefacts that are never part of a solution.
DEFAULT_IGNORE: tuple[str, ...] = (
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.mypy_cache/**",
    "**/.git/**",
    "**/*.egg-info/**",
    "**/node_modules/**",
    "**/.DS_Store",
    "**/.coverage",
    "**/htmlcov/**",
    "**/.tox/**",
    "**/.venv/**",
)

_MAX_FILE_BYTES = 2_000_000

#: Paths that control *how tests are selected and which plugins load*, rather
#: than what the code does.
#:
#: A patch touching any of these is rejected by the grading engine regardless of
#: the task's own ``forbidden_paths``, because a verdict computed under
#: agent-supplied runner configuration means nothing. This is not hypothetical:
#: writing a plugin module at the repository root and adding
#: ``addopts = -p <module>`` to any one of these files makes every graded test
#: pass without touching ``tests/`` or a ``conftest.py`` at all.
#:
#: Tasks that genuinely need to change dependencies should use
#: ``environment.pip_packages`` or ``environment.setup_commands``, which run
#: before the agent starts and are not agent-controlled.
GRADING_INTEGRITY_PATHS: tuple[str, ...] = (
    "pytest.ini",
    "**/pytest.ini",
    "setup.cfg",
    "**/setup.cfg",
    "tox.ini",
    "**/tox.ini",
    "pyproject.toml",
    "**/pyproject.toml",
    "conftest.py",
    "**/conftest.py",
)


@dataclass(slots=True)
class FileChange:
    path: str
    change: str  # "added" | "deleted" | "modified"
    insertions: int = 0
    deletions: int = 0
    binary: bool = False


@dataclass(slots=True)
class PatchResult:
    diff: str
    changes: list[FileChange] = field(default_factory=list)
    #: True when the trees are identical after filtering ignored paths.
    empty: bool = True
    truncated: bool = False

    @property
    def files_changed(self) -> int:
        return len(self.changes)

    @property
    def insertions(self) -> int:
        return sum(c.insertions for c in self.changes)

    @property
    def deletions(self) -> int:
        return sum(c.deletions for c in self.changes)

    @property
    def modified_paths(self) -> list[str]:
        return [c.path for c in self.changes]

    def stats(self) -> dict[str, object]:
        return {
            "files_changed": self.files_changed,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "binary_files": [c.path for c in self.changes if c.binary],
            "paths": [
                {"path": c.path, "change": c.change, "+": c.insertions, "-": c.deletions}
                for c in self.changes
            ],
            "diff_sha256": hashlib.sha256(self.diff.encode()).hexdigest() if self.diff else None,
            "truncated": self.truncated,
        }


def is_ignored(rel_path: str, patterns: tuple[str, ...] | list[str] = DEFAULT_IGNORE) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(f"/{rel_path}", f"/{pattern}"):
            return True
        # Match a bare directory pattern against any ancestor segment.
        if pattern.startswith("**/") and pattern.endswith("/**"):
            segment = pattern[3:-3]
            if segment and segment in Path(rel_path).parts:
                return True
    return False


def _walk(root: Path, patterns: tuple[str, ...] | list[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if not root.is_dir():
        return files
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if is_ignored(rel, patterns):
            continue
        files[rel] = path
    return files


def _read(path: Path) -> tuple[list[str] | None, bytes]:
    """Return ``(lines, raw)``; ``lines`` is ``None`` for binary content."""
    raw = path.read_bytes()[:_MAX_FILE_BYTES]
    if b"\x00" in raw[:8192]:
        return None, raw
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, raw
    return text.splitlines(keepends=True), raw


def compute_patch(
    before_root: Path,
    after_root: Path,
    *,
    ignore: tuple[str, ...] | list[str] = DEFAULT_IGNORE,
    max_diff_chars: int = 400_000,
) -> PatchResult:
    """Unified diff taking ``before_root`` to ``after_root``."""
    before = _walk(before_root, ignore)
    after = _walk(after_root, ignore)

    result = PatchResult(diff="", changes=[], empty=True)
    chunks: list[str] = []
    total = 0
    truncated = False

    for rel in sorted(set(before) | set(after)):
        in_before, in_after = rel in before, rel in after
        before_lines, before_raw = _read(before[rel]) if in_before else ([], b"")
        after_lines, after_raw = _read(after[rel]) if in_after else ([], b"")

        if in_before and in_after and before_raw == after_raw:
            continue

        change = "modified" if (in_before and in_after) else ("added" if in_after else "deleted")

        if before_lines is None or after_lines is None:
            result.changes.append(FileChange(rel, change, binary=True))
            chunks.append(f"diff --git a/{rel} b/{rel}\nBinary files a/{rel} and b/{rel} differ\n")
            continue

        diff_lines = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{rel}" if in_before else "/dev/null",
                tofile=f"b/{rel}" if in_after else "/dev/null",
                lineterm="\n",
                n=3,
            )
        )
        if not diff_lines:
            continue

        insertions = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
        deletions = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
        result.changes.append(FileChange(rel, change, insertions, deletions))

        body = f"diff --git a/{rel} b/{rel}\n" + "".join(diff_lines)
        if not body.endswith("\n"):
            body += "\n\\ No newline at end of file\n"
        if total + len(body) > max_diff_chars:
            truncated = True
            chunks.append(f"diff --git a/{rel} b/{rel}\n[diff omitted: size budget exceeded]\n")
            continue
        total += len(body)
        chunks.append(body)

    result.diff = "".join(chunks)
    result.empty = not result.changes
    result.truncated = truncated
    return result


def check_forbidden_paths(paths: list[str], forbidden: list[str]) -> list[str]:
    """Return the subset of ``paths`` matching any ``forbidden`` glob.

    This is what stops an agent from "fixing" a task by editing or deleting the
    tests that grade it.
    """
    violations: list[str] = []
    for path in paths:
        for pattern in forbidden:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(f"/{path}", f"/{pattern}"):
                violations.append(path)
                break
            # `tests/**` should also match the directory's direct children.
            if pattern.endswith("/**") and path.startswith(pattern[:-2]):
                violations.append(path)
                break
    return sorted(set(violations))
