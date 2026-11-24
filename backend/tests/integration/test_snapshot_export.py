"""Static snapshot export (``agentbench snapshot export``).

The snapshot must be the API's own output: same bytes, same status, every
page of every list, and nothing describing the live host. These tests pin
that, plus the request-key rule the frontend mirrors in ``api/snapshot.ts``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from agentbench import snapshot
from agentbench.schemas.enums import RunOutcome
from agentbench.snapshot import (
    API_PREFIX,
    MANIFEST,
    SnapshotError,
    SnapshotWriter,
    export_snapshot,
    request_key,
)
from tests.integration.test_insights_api import _experiment


def _read(out: Path, manifest: dict[str, Any], key: str) -> bytes:
    return (out / manifest["entries"][key]["file"]).read_bytes()


class TestRequestKey:
    def test_parameters_are_sorted_raw_and_empty_ones_dropped(self) -> None:
        key = request_key(
            "/leaderboard",
            {"include_non_model": True, "benchmark_id": "textkit", "experiment_ids": None, "q": ""},
        )
        assert key == "/leaderboard?benchmark_id=textkit&include_non_model=true"

    def test_no_parameters_is_just_the_path(self) -> None:
        assert request_key("/tasks/textkit-ordinal@1.0", {}) == "/tasks/textkit-ordinal@1.0"
        assert request_key("/overview") == "/overview"

    def test_values_are_not_url_encoded(self) -> None:
        assert request_key("/compare/matrix", {"experiment_ids": "a,b"}) == (
            "/compare/matrix?experiment_ids=a,b"
        )


class TestExport:
    @pytest.fixture
    def two_experiments(self, db: Session, seeded_task: Any) -> list[str]:
        first = _experiment(
            db,
            agent_name="live-a",
            task_keys=[seeded_task.key],
            repetitions=3,
            results=[
                (RunOutcome.SOLVED, 10.0),
                (RunOutcome.TIMEOUT, 30.0),
                (RunOutcome.SOLVED, 12.0),
            ],
        )
        second = _experiment(
            db,
            agent_name="live-b",
            task_keys=[seeded_task.key],
            repetitions=2,
            results=[(RunOutcome.UNSOLVED, 20.0), (RunOutcome.SOLVED, 15.0)],
        )
        return [first.id, second.id]

    def test_responses_are_the_api_bytes_and_cover_every_view(
        self, api_client: Any, two_experiments: list[str], seeded_task: Any, tmp_path: Path
    ) -> None:
        out = tmp_path / "snapshot"
        manifest = export_snapshot(api_client, out)
        assert json.loads((out / MANIFEST).read_text()) == manifest
        entries = manifest["entries"]

        assert (
            _read(out, manifest, "/system/stats")
            == api_client.get(f"{API_PREFIX}/system/stats").content
        )
        # These stamp `computed_at` per request; everything else must match.
        for key in ("/overview", "/leaderboard", "/analytics"):
            exported = json.loads(_read(out, manifest, key))
            live = api_client.get(API_PREFIX + key).json()
            assert {**exported, "computed_at": None} == {**live, "computed_at": None}

        first, second = two_experiments
        for experiment_id in two_experiments:
            assert f"/experiments/{experiment_id}/metrics" in entries
            assert f"/experiments/{experiment_id}/report?format=csv" in entries
            assert f"/experiments/{experiment_id}/runs" in manifest["collections"]
        # Both orders of every pair, for the two-arm view and the matrix.
        for baseline, candidate in ((first, second), (second, first)):
            assert f"/compare?baseline={baseline}&candidate={candidate}" in entries
            assert f"/compare/matrix?experiment_ids={baseline},{candidate}" in entries
        assert f"/tasks/{seeded_task.key}/history" in entries
        assert "/leaderboard?benchmark_id=minirepo&include_non_model=true" in entries
        assert manifest["counts"]["runs"] == 5

    def test_reports_keep_their_media_type(
        self, api_client: Any, two_experiments: list[str], tmp_path: Path
    ) -> None:
        manifest = export_snapshot(api_client, tmp_path / "s")
        entry = manifest["entries"][f"/experiments/{two_experiments[0]}/report?format=markdown"]
        assert entry["type"] == "text/markdown" and entry["file"].endswith(".md")

    def test_live_host_views_are_not_exported(
        self, api_client: Any, two_experiments: list[str], tmp_path: Path
    ) -> None:
        manifest = export_snapshot(api_client, tmp_path / "s")
        for key in ("/system/resources", "/system/docker", "/system/local-models"):
            assert key not in manifest["entries"]

    def test_collections_hold_every_page_in_server_order(
        self,
        api_client: Any,
        two_experiments: list[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(snapshot, "MAX_PAGE", 2)  # 5 runs → 3 pages
        out = tmp_path / "s"
        manifest = export_snapshot(api_client, out)
        exported = json.loads((out / manifest["collections"]["/runs"]).read_text())
        live = api_client.get(f"{API_PREFIX}/runs", params={"limit": 200}).json()["items"]
        assert [run["id"] for run in exported["items"]] == [run["id"] for run in live]
        assert exported["total"] == 5

    def test_a_previous_snapshot_is_replaced_but_other_folders_are_refused(
        self, api_client: Any, two_experiments: list[str], tmp_path: Path
    ) -> None:
        out = tmp_path / "s"
        export_snapshot(api_client, out)
        (out / "files" / "stale.json").write_text("{}")
        export_snapshot(api_client, out)
        assert not (out / "files" / "stale.json").exists()

        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("mine")
        with pytest.raises(SnapshotError, match="refusing to overwrite"):
            export_snapshot(api_client, unrelated)
        assert (unrelated / "keep.txt").read_text() == "mine"


class _Fake:
    def __init__(self, status: int, body: bytes) -> None:
        self.status_code = status
        self.content = body
        self.headers: Mapping[str, str] = {"content-type": "application/json"}

    def json(self) -> Any:
        return json.loads(self.content)


class _FakeClient:
    def __init__(self, response: _Fake) -> None:
        self.response = response

    def get(self, url: str, params: Any = None) -> _Fake:
        return self.response


class TestWriter:
    def test_a_server_error_stops_the_export(self, tmp_path: Path) -> None:
        writer = SnapshotWriter(client=_FakeClient(_Fake(500, b"{}")), out_dir=tmp_path)
        with pytest.raises(SnapshotError, match="500"):
            writer.get("/overview")

    def test_a_client_error_is_recorded_with_its_status(self, tmp_path: Path) -> None:
        body = b'{"error":{"code":"http_400","message":"incompatible"}}'
        writer = SnapshotWriter(client=_FakeClient(_Fake(400, body)), out_dir=tmp_path)
        writer.get("/compare", {"baseline": "a", "candidate": "b"})
        entry = writer.entries["/compare?baseline=a&candidate=b"]
        assert entry["status"] == 400
        assert (tmp_path / entry["file"]).read_bytes() == body

    def test_the_home_directory_is_published_as_a_tilde(self, tmp_path: Path) -> None:
        body = b'{"detail":"socket at unix:///Users/someone/.docker/run/docker.sock"}'
        writer = SnapshotWriter(
            client=_FakeClient(_Fake(200, body)), out_dir=tmp_path, home="/Users/someone"
        )
        writer.get("/health")
        written = (tmp_path / writer.entries["/health"]["file"]).read_bytes()
        assert b"/Users/someone" not in written
        assert b"unix://~/.docker/run/docker.sock" in written
