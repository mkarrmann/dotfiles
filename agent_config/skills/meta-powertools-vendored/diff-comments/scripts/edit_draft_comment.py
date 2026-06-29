#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict
from __future__ import annotations

import argparse
import os
import sys

from diff_comments_utils import run_jf_graphql


def edit_draft_comment(
    fbid: str,
    message: str,
    publish: bool = False,
    unset_suggested_change: bool = False,
    suggested: str | None = None,
) -> None:
    mutation = """
mutation EditDraftCommentMutation($input: DifferentialInlineDraftEditData!) {
  differential_inline_draft_edit(data: $input) {
    comment {
      id
      content
      line_number
    }
  }
}
"""

    variables = {
        "input": {
            "inline_comment_id": fbid,
            "content": message,
            "content_format": "MARKUP",
            "should_unset_suggested_change": unset_suggested_change,
            "client_caller": "diff_comments_skill",
            "skip_draft": publish,
        }
    }

    if suggested is not None:
        variables["input"]["suggested_content"] = suggested

    try:
        result = run_jf_graphql(mutation, variables)
        comment = result.get("differential_inline_draft_edit", {}).get("comment")
        if not comment:
            raise RuntimeError("No comment returned from mutation")
        action = "Published" if publish else "Updated draft"
        print(f"success {action} comment {fbid}.")
    except RuntimeError as e:
        print(f"Error: Failed to edit draft comment {fbid}", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Edit a draft inline comment on a Phabricator diff"
    )
    parser.add_argument(
        "--fbid",
        required=True,
        help="Draft comment FBID to edit",
    )
    parser.add_argument(
        "--message",
        required=True,
        help="New comment text",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the comment after editing (skip draft state)",
    )
    parser.add_argument(
        "--unset-suggested-change",
        action="store_true",
        help="Strip any suggested change block attached to the draft",
    )
    suggest_group = parser.add_mutually_exclusive_group()
    suggest_group.add_argument(
        "--suggested",
        help="Set suggested replacement code on the draft comment",
    )
    suggest_group.add_argument(
        "--suggested-file",
        help="Path to file containing suggested replacement code",
    )

    args = parser.parse_args()

    if args.suggested_file and not os.path.isfile(args.suggested_file):
        parser.error(f"--suggested-file path does not exist: {args.suggested_file}")

    return args


def main() -> None:
    args = parse_args()

    suggested = args.suggested
    if args.suggested_file:
        with open(args.suggested_file) as f:
            suggested = f.read()

    try:
        edit_draft_comment(
            args.fbid,
            args.message,
            args.publish,
            args.unset_suggested_change,
            suggested,
        )
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
