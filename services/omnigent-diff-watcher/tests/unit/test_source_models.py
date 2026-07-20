from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from omnigent_diff_watcher.source_models import (
    DiffLifecycle,
    DiffSnapshot,
    SourceCursor,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
VALID_FIXTURES = (
    "active.json",
    "failing.json",
    "green.json",
    "committed.json",
    "missing.json",
    "partial_failure.json",
)
SYNTHETIC_DIFF_IDS = {f"D9000000{index}" for index in range(1, 7)}


@pytest.mark.parametrize("fixture_name", VALID_FIXTURES)
def test_parse_sanitized_fixture(fixture_name: str) -> None:
    snapshot = DiffSnapshot.model_validate_json((FIXTURES / fixture_name).read_bytes())

    assert snapshot.diff_id in SYNTHETIC_DIFF_IDS
    assert snapshot.observed_at.tzinfo is not None


def test_active_snapshot_requires_identity_and_version() -> None:
    payload = json.loads((FIXTURES / "active.json").read_text())
    payload["author_id"] = None

    with pytest.raises(ValidationError, match="known diff requires"):
        DiffSnapshot.model_validate(payload)

    payload = json.loads((FIXTURES / "active.json").read_text())
    payload["latest_version_id"] = None
    with pytest.raises(ValidationError, match="known diff requires"):
        DiffSnapshot.model_validate(payload)


def test_missing_snapshot_rejects_claimed_identity() -> None:
    payload = json.loads((FIXTURES / "missing.json").read_text())
    payload["author_id"] = "user-author"

    with pytest.raises(ValidationError, match="missing diff cannot include"):
        DiffSnapshot.model_validate(payload)


def test_partial_failure_advances_only_successful_cursor() -> None:
    snapshot = DiffSnapshot.model_validate_json((FIXTURES / "partial_failure.json").read_bytes())
    previous = SourceCursor(
        latest_version_id="version-partial-1",
        comments="comments-partial-1",
        ci="ci-partial-1",
    )

    assert snapshot.cursor(previous) == SourceCursor(
        latest_version_id="version-partial-2",
        comments="comments-partial-2",
        ci="ci-partial-1",
    )


def test_lifecycle_terminal_property() -> None:
    assert not DiffLifecycle.ACTIVE.terminal
    assert all(
        lifecycle.terminal for lifecycle in DiffLifecycle if lifecycle is not DiffLifecycle.ACTIVE
    )


def test_fixtures_contain_only_synthetic_data() -> None:
    denylist = re.compile(
        r"internalfb|facebook|meta\.com|mkarrmann|/data/|/home/|fbcode|www/|https?://",
        re.IGNORECASE,
    )
    for fixture in FIXTURES.glob("*.json"):
        text = fixture.read_text()
        assert denylist.search(text) is None, fixture.name
        observed = set(re.findall(r"D[1-9][0-9]*", text))
        assert observed <= SYNTHETIC_DIFF_IDS, fixture.name
