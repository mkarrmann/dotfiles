#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict
from __future__ import annotations

import argparse
import os
import sys

from diff_comments_utils import add_ai_attribution, get_diff_fbid, run_jf_graphql
from inline_comment import add_inline_comment, get_first_changeset


def _mutation_succeeded(result: object, mutation_field: str) -> bool:
    """Return True if the GraphQL response indicates the mutation actually ran.

    `jf graphql` exits 0 even when a mutation is silently rejected server-side
    (e.g. validation failure on `action`/`attach_inlines`), so we must inspect
    the response body. A successful mutation returns a non-null payload under
    the named mutation field; we treat anything else as failure.
    """
    if not isinstance(result, dict):
        return False
    payload = result.get(mutation_field)
    return isinstance(payload, dict) and bool(payload)


def save_draft_comment(diff_number: str, message: str) -> None:
    diff_num = diff_number.lstrip("D")
    diff_fbid = get_diff_fbid(diff_num)

    mutation = """
mutation PhabricatorDiffDraftChangeEditMutation($input: PhabricatorDiffDraftChangeEditData!) {
  phabricator_diff_draft_change_edit(data: $input) {
    phabricator_diff {
      id
      number
    }
  }
}
"""
    variables = {
        "input": {
            "phabricator_diff_id": diff_fbid,
            "diff_field_key": "COMMENT",
            "field_value": message,
            "client_caller": "diff_comments_skill",
        }
    }
    try:
        result = run_jf_graphql(mutation, variables)
    except RuntimeError as e:
        print("Error: Failed to save draft comment", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(1)
    else:
        if not _mutation_succeeded(result, "phabricator_diff_draft_change_edit"):
            print(
                f"Error: GraphQL mutation returned no payload; draft was not "
                f"saved on D{diff_num}",
                file=sys.stderr,
            )
            print(f"Mutation response: {result}", file=sys.stderr)
            sys.exit(1)
        print(f"success Saved draft comment on D{diff_num}")


def _get_comment_count(diff_number: str) -> int:
    """Return the diff's published comment count, or -1 if it can't be determined.

    Used to VERIFY a comment actually published (the old top-level path printed
    "success" even when nothing was posted).
    """
    diff_num = diff_number.lstrip("D")
    query = """
query DiffCommentCount($number: String!) {
  phabricator_diff(number: $number) {
    diff_comments {
      count
    }
  }
}
"""
    try:
        result = run_jf_graphql(query, {"number": diff_num})
        return int(result["phabricator_diff"]["diff_comments"]["count"])
    except (RuntimeError, KeyError, TypeError, ValueError):
        return -1


def post_top_level_comment(
    diff_number: str,
    message: str,
    # accepted for backward-compat; submitting always flushes pending inlines.
    attach_inlines: bool = False,
    ai_signature: bool = False,
) -> None:
    # There is no working GraphQL path to post a *true* top-level diff comment:
    # `update_phabricator_diff` silently drops the `comment` field with action
    # "none" (the mutation still returns a non-null payload, so it falsely looks
    # successful), and submitting does not flush the top-level COMMENT draft.
    # The reliable, verified path is an inline comment + review-action submit, so
    # anchor the comment to the first changed line of the diff — it still reads as
    # a comment in the diff's conversation.
    diff_num = diff_number.lstrip("D")
    try:
        filename, _ = get_first_changeset(diff_num)
    except RuntimeError as e:
        print(f"Error: cannot post comment on D{diff_num}: {e}", file=sys.stderr)
        raise SystemExit(1) from e

    before = _get_comment_count(diff_num)

    # add_inline_comment creates the draft and submits a review action to publish.
    # Restore the clean exit-1 contract: a publish failure (changeset lookup,
    # GraphQL error, network timeout, ...) must surface as a user-readable error
    # and a non-zero exit, not an unhandled traceback.
    try:
        add_inline_comment(
            diff_num,
            filename,
            1,
            message=message,
            ai_signature=ai_signature,
            submit=True,
        )
    except RuntimeError as e:
        print(
            f"Error: failed to publish comment on D{diff_num}: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Verify it actually published; fail loudly if the count did not increase.
    after = _get_comment_count(diff_num)
    if before >= 0 and after >= 0 and after <= before:
        print(
            f"Error: comment did not publish on D{diff_num} "
            f"(comment count unchanged: {before} -> {after})",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"success Comment published on D{diff_num} "
        f"(anchored to {filename}:1; comment count {before} -> {after})"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Post a top-level comment on a Phabricator diff"
    )
    parser.add_argument("diff_number", help="Diff number (e.g., D12345)")

    message_group = parser.add_mutually_exclusive_group(required=True)
    message_group.add_argument("--message", help="Comment text")
    message_group.add_argument(
        "--message-file", help="Path to file containing comment text"
    )

    parser.add_argument(
        "--draft",
        action="store_true",
        help="Save as draft comment instead of publishing immediately",
    )
    parser.add_argument(
        "--attach-inlines",
        action="store_true",
        help="Also publish any pending inline draft comments",
    )
    parser.add_argument(
        "--ai-signature",
        action="store_true",
        help="Append 'Sent from Claude Code' signature to the comment",
    )

    args = parser.parse_args()
    if args.message_file and not os.path.isfile(args.message_file):
        parser.error(f"--message-file path does not exist: {args.message_file}")
    if args.draft and args.attach_inlines:
        parser.error("--draft cannot be combined with --attach-inlines")
    return args


def main() -> None:
    args = parse_args()

    message = args.message
    if args.message_file:
        with open(args.message_file) as f:
            message = f.read()

    if args.draft:
        comment_body = add_ai_attribution(message, args.ai_signature)
        save_draft_comment(args.diff_number, comment_body)
    else:
        post_top_level_comment(
            args.diff_number,
            message,
            args.attach_inlines,
            args.ai_signature,
        )


if __name__ == "__main__":
    main()
