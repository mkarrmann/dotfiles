from __future__ import annotations

import json
from pathlib import Path

import httpx

from omnigent_diff_watcher.domain import DEFAULT_EVENT_TYPES, WatcherConfig
from omnigent_diff_watcher.omnigent_client import OmnigentClient, OmnigentDeliveryService
from omnigent_diff_watcher.repository import WatcherRepository
from omnigent_diff_watcher.watcher import DiffWatcher
from tests.support import FakeClock, FakeReviewSource, fixture


async def test_label_subscription_batches_and_posts_one_existing_api_event(
    tmp_path: Path,
) -> None:
    posts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"data": []})
        if request.method == "POST" and request.url.path.endswith("/events"):
            posts.append(json.loads(request.content))
            return httpx.Response(202, json={"accepted": True})
        return httpx.Response(
            200,
            json={
                "id": "conv_watch",
                "status": "idle",
                "labels": {
                    "omnigent.diff.number": "D90000001",
                    "omnigent.diff.watch": "ci_failure,review_comment",
                },
                "runner_id": "runner",
                "runner_online": True,
                "pending_elicitations": [],
                "pending_inputs": [],
            },
        )

    raw_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://server",
    )
    client = OmnigentClient("http://unused", client=raw_client)
    active = fixture("active")
    failing_raw = fixture("failing")
    failing = failing_raw.model_copy(
        update={
            "diff_id": active.diff_id,
            "latest_version_id": active.latest_version_id,
            "comments": failing_raw.comments.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(update={"version_id": active.latest_version_id})
                        for item in failing_raw.comments.items
                    )
                }
            ),
        }
    )
    committed = fixture("committed").model_copy(
        update={"diff_id": active.diff_id, "latest_version_id": active.latest_version_id}
    )
    source = FakeReviewSource(active, failing, failing, committed)
    clock = FakeClock()
    repository = WatcherRepository(tmp_path / "watcher.sqlite3")
    watcher = DiffWatcher(
        repository,
        source,
        client,
        OmnigentDeliveryService(client, mode="enabled", allowlist=frozenset()),
        clock=clock,
        config=WatcherConfig(
            batch_window_seconds=5,
            minimum_delivery_interval_seconds=10,
            poll_interval_override_seconds=1,
            delivery_retry_seconds=1,
        ),
    )
    await watcher.subscribe("conv_watch", active.diff_id, DEFAULT_EVENT_TYPES)

    clock.advance(2)
    await watcher.run_iteration()
    assert posts == []

    clock.advance(6)
    await watcher.run_iteration()
    assert len(posts) == 1
    text = posts[0]["data"]["content"][0]["text"]  # type: ignore[index]
    assert "unresolved review" in text
    assert "current-version CI" in text
    assert "comment body" not in text

    clock.advance(2)
    await watcher.run_iteration()
    assert len(posts) == 1
    assert repository.subscription("conv_watch").retired_reason == "committed"  # type: ignore[union-attr]
    await raw_client.aclose()
