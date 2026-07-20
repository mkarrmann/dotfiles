from __future__ import annotations

import os
import sys

import pytest

from omnigent_diff_watcher.source_command import (
    SourceCommandError,
    SourceCommandErrorCategory,
    run_json_command,
)


@pytest.mark.asyncio
async def test_timeout_kills_command_and_redacts_output() -> None:
    with pytest.raises(SourceCommandError) as exc_info:
        await run_json_command(
            (sys.executable, "-c", "import time; print('private'); time.sleep(30)"),
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=0.05,
        )
    assert exc_info.value.category is SourceCommandErrorCategory.TIMEOUT
    assert "private" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_output_cap_fails_without_returning_output() -> None:
    with pytest.raises(SourceCommandError) as exc_info:
        await run_json_command(
            (sys.executable, "-c", "print('x' * 10000)"),
            env={"PATH": os.environ["PATH"]},
            output_limit_bytes=100,
        )
    assert exc_info.value.category is SourceCommandErrorCategory.OUTPUT_LIMIT
    assert "xxxxx" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_nonzero_exit_and_malformed_json_are_redacted() -> None:
    with pytest.raises(SourceCommandError) as exit_error:
        await run_json_command(
            (sys.executable, "-c", "import sys; print('secret'); sys.exit(7)"),
            env={"PATH": os.environ["PATH"]},
        )
    assert exit_error.value.category is SourceCommandErrorCategory.EXIT
    assert "secret" not in str(exit_error.value)

    with pytest.raises(SourceCommandError) as malformed:
        await run_json_command(
            (sys.executable, "-c", "print('not-json-secret')"),
            env={"PATH": os.environ["PATH"]},
        )
    assert malformed.value.category is SourceCommandErrorCategory.MALFORMED
    assert "not-json-secret" not in str(malformed.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "category"),
    (
        ("OAuth token expired: secret-token", SourceCommandErrorCategory.AUTH),
        ("Too many requests: secret-limit", SourceCommandErrorCategory.RATE_LIMIT),
    ),
)
async def test_expected_failure_categories_are_classified_without_detail(
    stderr: str,
    category: SourceCommandErrorCategory,
) -> None:
    with pytest.raises(SourceCommandError) as exc_info:
        await run_json_command(
            (
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1], file=sys.stderr); raise SystemExit(1)",
                stderr,
            ),
            env={"PATH": os.environ["PATH"]},
        )

    assert exc_info.value.category is category
    assert "secret" not in str(exc_info.value)
