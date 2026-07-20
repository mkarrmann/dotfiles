from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from omnigent_diff_watcher.source_command import (
    SourceCommandError,
    SourceCommandErrorCategory,
    run_snapshot_command,
)

TESTS = Path(__file__).parents[1]
FIXTURES = TESTS / "fixtures"
FAKE_SOURCE = TESTS / "fake_review_source.py"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_name",
    (
        "active.json",
        "failing.json",
        "green.json",
        "committed.json",
        "missing.json",
        "partial_failure.json",
    ),
)
async def test_fixture_crosses_real_subprocess_boundary(fixture_name: str) -> None:
    snapshot = await run_snapshot_command(
        (
            sys.executable,
            str(FAKE_SOURCE),
            "--fixture-dir",
            str(FIXTURES),
            "--fixture",
            fixture_name,
        ),
        env={"PATH": os.environ["PATH"]},
    )

    assert snapshot.schema_version == 1


@pytest.mark.asyncio
async def test_malformed_fixture_is_redacted() -> None:
    with pytest.raises(SourceCommandError) as exc_info:
        await run_snapshot_command(
            (
                sys.executable,
                str(FAKE_SOURCE),
                "--fixture-dir",
                str(FIXTURES),
                "--fixture",
                "malformed.json",
            ),
            env={"PATH": os.environ["PATH"]},
        )

    assert exc_info.value.category is SourceCommandErrorCategory.MALFORMED
    assert "this is not valid JSON" not in str(exc_info.value)
