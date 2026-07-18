from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from omnigent_google_chat.meta_chat import MetaChatTimeoutError, MetaGoogleChatClient


def make_executable(path: Path, source: str) -> Path:
    path.write_text(source)
    path.chmod(0o700)
    return path


async def test_real_subprocess_adapter_passes_body_only_through_stdin(tmp_path: Path) -> None:
    record = tmp_path / "record"
    executable = make_executable(
        tmp_path / "fake-meta",
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys

body = sys.stdin.read()
pathlib.Path({str(record)!r}).write_text(json.dumps({{"argv": sys.argv[1:], "body": body}}))
print(json.dumps({{
    "name": "spaces/s/messages/m",
    "space": {{"name": "spaces/s"}},
    "thread": {{"name": "spaces/s/threads/t"}},
    "sender": {{"name": "users/bot", "type": "BOT"}},
    "createTime": "2026-01-01T00:00:00Z",
    "text": body,
}}))
""",
    )
    client = MetaGoogleChatClient(executable=executable, space_name="spaces/s")
    text = "literal $HOME; $(touch should-not-run)"
    sent = await client.send_message(text=text, request_id="request")
    assert sent.name == "spaces/s/messages/m"
    recorded = json.loads(record.read_text())
    assert recorded["body"] == text
    assert text not in recorded["argv"]
    assert not (tmp_path / "should-not-run").exists()


async def test_real_subprocess_timeout_kills_command(tmp_path: Path) -> None:
    executable = make_executable(
        tmp_path / "slow-meta",
        """#!/usr/bin/env python3
import time
time.sleep(60)
""",
    )
    client = MetaGoogleChatClient(
        executable=executable,
        space_name="spaces/s",
        timeout_seconds=0.05,
    )
    with pytest.raises(MetaChatTimeoutError):
        await client.list_page(created_after="2026-01-01T00:00:00Z")


def test_fake_executable_is_owner_only(tmp_path: Path) -> None:
    executable = make_executable(tmp_path / "fake-meta", "#!/bin/sh\nexit 0\n")
    assert os.stat(executable).st_mode & 0o777 == 0o700
