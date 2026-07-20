from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from omnigent_diff_watcher.phabricator_source import (
    PhabricatorReviewSource,
)
from omnigent_diff_watcher.source_models import (
    CIAggregateState,
)

FAKE = Path(__file__).parents[1] / "fake_meta_cli.py"


@pytest.mark.asyncio
async def test_real_process_adapter_with_fake_jf_and_meta(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "fake-meta-cli"
    shutil.copyfile(FAKE, executable)
    executable.chmod(0o700)
    (bin_dir / "jf").symlink_to(executable)
    (bin_dir / "meta").symlink_to(executable)

    source = PhabricatorReviewSource(
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "USER": "synthetic",
            "USERNAME": "synthetic",
        }
    )
    snapshot = await source.snapshot("D90000001", None)

    assert [comment.external_id for comment in snapshot.comments.items] == ["comment-synthetic"]
    assert snapshot.ci.aggregate is CIAggregateState.FAILING
    assert snapshot.ci.failures[0].external_id.startswith("signal:")
