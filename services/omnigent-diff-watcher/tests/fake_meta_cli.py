#!/usr/bin/env python3
"""Synthetic jf/meta executable for adapter integration tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    executable = Path(sys.argv[0]).name
    args = sys.argv[1:]
    payload: object
    if executable == "jf" and args[:1] == ["diff-properties"]:
        payload = {
            "number": 90000001,
            "status": "Needs Review",
            "is_closed": False,
            "created_time": 1768478400,
            "author": {"id": "author-synthetic", "unixname": "author"},
            "latest_phabricator_version": {"id": "version-7", "number": 7},
            "latest_draft_phabricator_version": None,
        }
    elif executable == "jf" and args[:1] == ["graphql"]:
        payload = {
            "signalview_signals": {
                "all": {"count": 3},
                "failed": {
                    "count": 1,
                    "nodes": [
                        {
                            "name": "synthetic-unit-test",
                            "status": "FAILED",
                            "slp_functional_type": "TEST",
                        }
                    ],
                },
                "pending": {"count": 0},
            }
        }
    elif executable == "meta" and args[:2] == ["phabricator.diff", "comments"]:
        payload = [
            {
                "id": "comment-synthetic",
                "version_id": "version-7",
                "updated_at": "2026-01-15T12:02:00Z",
                "author": {"id": "reviewer-synthetic"},
                "content": "Synthetic actionable comment",
            }
        ]
    else:
        print("unexpected synthetic command", file=sys.stderr)
        return 2
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
