"""Workspace diffing and protected-path enforcement."""

from __future__ import annotations

from pathlib import Path

from agentbench.grading.patch import (
    DEFAULT_IGNORE,
    check_forbidden_paths,
    compute_patch,
    is_ignored,
)


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestComputePatch:
    def test_identical_trees_produce_an_empty_patch(self, tmp_path: Path) -> None:
        before, after = tmp_path / "a", tmp_path / "b"
        for root in (before, after):
            write(root, "src/mod.py", "x = 1\n")
        patch = compute_patch(before, after)
        assert patch.empty
        assert patch.diff == ""
        assert patch.files_changed == 0

    def test_modified_file_is_reported_with_line_counts(self, tmp_path: Path) -> None:
        before, after = tmp_path / "a", tmp_path / "b"
        write(before, "src/mod.py", "x = 1\ny = 2\n")
        write(after, "src/mod.py", "x = 1\ny = 3\n")
        patch = compute_patch(before, after)
        assert not patch.empty
        assert patch.files_changed == 1
        assert patch.insertions == 1
        assert patch.deletions == 1
        assert patch.changes[0].change == "modified"
        assert "diff --git a/src/mod.py b/src/mod.py" in patch.diff
        assert "+y = 3" in patch.diff

    def test_added_file_uses_dev_null_as_the_source(self, tmp_path: Path) -> None:
        before, after = tmp_path / "a", tmp_path / "b"
        before.mkdir()
        write(after, "src/new.py", "print('hi')\n")
        patch = compute_patch(before, after)
        assert patch.changes[0].change == "added"
        assert "--- /dev/null" in patch.diff

    def test_deleted_file_is_detected(self, tmp_path: Path) -> None:
        before, after = tmp_path / "a", tmp_path / "b"
        write(before, "src/gone.py", "x = 1\n")
        after.mkdir()
        patch = compute_patch(before, after)
        assert patch.changes[0].change == "deleted"
        assert "+++ /dev/null" in patch.diff

    def test_build_artifacts_are_ignored(self, tmp_path: Path) -> None:
        before, after = tmp_path / "a", tmp_path / "b"
        write(before, "src/mod.py", "x = 1\n")
        write(after, "src/mod.py", "x = 1\n")
        write(after, "src/__pycache__/mod.cpython-312.pyc", "bytes")
        write(after, ".pytest_cache/v/cache/lastfailed", "{}")
        assert compute_patch(before, after).empty

    def test_binary_change_is_flagged_not_inlined(self, tmp_path: Path) -> None:
        before, after = tmp_path / "a", tmp_path / "b"
        (before / "assets").mkdir(parents=True)
        (after / "assets").mkdir(parents=True)
        (before / "assets" / "blob.bin").write_bytes(b"\x00\x01\x02")
        (after / "assets" / "blob.bin").write_bytes(b"\x00\x09\x09")
        patch = compute_patch(before, after)
        assert patch.changes[0].binary is True
        assert "Binary files" in patch.diff

    def test_diff_is_deterministic_for_the_same_trees(self, tmp_path: Path) -> None:
        before, after = tmp_path / "a", tmp_path / "b"
        write(before, "z.py", "1\n")
        write(before, "a.py", "1\n")
        write(after, "z.py", "2\n")
        write(after, "a.py", "2\n")
        first = compute_patch(before, after)
        second = compute_patch(before, after)
        assert first.diff == second.diff
        assert first.stats()["diff_sha256"] == second.stats()["diff_sha256"]

    def test_large_diff_is_truncated_but_reported(self, tmp_path: Path) -> None:
        before, after = tmp_path / "a", tmp_path / "b"
        write(before, "big.py", "x\n" * 10)
        write(after, "big.py", "y\n" * 5000)
        patch = compute_patch(before, after, max_diff_chars=500)
        assert patch.truncated is True
        assert patch.stats()["truncated"] is True

    def test_stats_lists_every_changed_path(self, tmp_path: Path) -> None:
        before, after = tmp_path / "a", tmp_path / "b"
        write(before, "one.py", "a\n")
        write(after, "one.py", "b\n")
        write(after, "two.py", "c\n")
        stats = compute_patch(before, after).stats()
        assert {entry["path"] for entry in stats["paths"]} == {"one.py", "two.py"}


class TestIgnorePatterns:
    def test_pycache_anywhere_is_ignored(self) -> None:
        assert is_ignored("deep/nested/__pycache__/x.pyc", DEFAULT_IGNORE)

    def test_source_file_is_not_ignored(self) -> None:
        assert not is_ignored("src/textkit/slug.py", DEFAULT_IGNORE)


class TestForbiddenPaths:
    def test_directory_glob_matches_nested_file(self) -> None:
        assert check_forbidden_paths(["tests/unit/test_x.py"], ["tests/**"]) == [
            "tests/unit/test_x.py"
        ]

    def test_directory_glob_matches_direct_child(self) -> None:
        assert check_forbidden_paths(["tests/test_x.py"], ["tests/**"]) == ["tests/test_x.py"]

    def test_exact_path_matches(self) -> None:
        assert check_forbidden_paths(["pyproject.toml"], ["pyproject.toml"]) == ["pyproject.toml"]

    def test_conftest_glob_matches_at_any_depth(self) -> None:
        assert check_forbidden_paths(["a/b/conftest.py"], ["**/conftest.py"]) == ["a/b/conftest.py"]

    def test_source_changes_are_allowed(self) -> None:
        assert check_forbidden_paths(["src/mod.py"], ["tests/**", "**/conftest.py"]) == []

    def test_violations_are_deduplicated_and_sorted(self) -> None:
        paths = ["tests/b.py", "tests/a.py", "tests/a.py"]
        assert check_forbidden_paths(paths, ["tests/**"]) == ["tests/a.py", "tests/b.py"]
