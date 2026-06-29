#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict
from __future__ import annotations

import argparse
import os
import sys

from diff_comments_utils import add_ai_attribution, CommentIntent, run_jf_graphql


def _candidate_versions(
    node: dict[str, object],
) -> list[dict[str, object]]:
    """Return versions to search for the requested file, in preference order.

    1. Latest PUBLISHED_VERSION from the `phabricator_versions` connection
       (visible to all reviewers — preferred when the file exists there).
    2. `latest_draft_phabricator_version` (covers two cases: an unpublished diff
       where no published version exists, AND draft-only files added after the
       last publish where the published version doesn't yet contain the file).
    """
    candidates: list[dict[str, object]] = []
    versions_conn = node.get("phabricator_versions")
    if isinstance(versions_conn, dict):
        nodes = versions_conn.get("nodes") or []
    else:
        nodes = []
    for v in nodes:
        if isinstance(v, dict) and v.get("version_status") == "PUBLISHED_VERSION":
            candidates.append(v)
            break  # latest published is at index 0; only need one
    draft = node.get("latest_draft_phabricator_version")
    if isinstance(draft, dict) and draft is not (candidates[0] if candidates else None):
        candidates.append(draft)
    return candidates


def _changesets_of(version: dict[str, object]) -> list[dict[str, object]]:
    """Extract the changeset list from a version dict, with type narrowing.

    Returns an empty list (rather than raising) when the version's structure is
    malformed; the caller decides whether that's an error or just a "no match here"
    while iterating multiple candidate versions.
    """
    container = version.get("phabricator_version_changesets")
    if not isinstance(container, dict):
        return []
    raw_nodes = container.get("nodes")
    if not isinstance(raw_nodes, list):
        return []
    return [c for c in raw_nodes if isinstance(c, dict)]


def _fetch_candidates(diff_number: str) -> list[dict[str, object]]:
    # Anchor comments to the latest *published* version so other reviewers viewing
    # the diff in the Phabricator UI can see them. Anchoring to drafts hides
    # comments from anyone but the diff author and creates ghost feedback on the
    # published view.
    #
    # The `phabricator_versions(...)` connection's filtering rules:
    #   - When a diff has any published versions, the connection only returns
    #     PUBLISHED_VERSION entries (drafts excluded). `first: 1` therefore
    #     gives us the latest published version.
    #   - When a diff is unpublished (no PUBLISHED_VERSION exists), the connection
    #     instead returns the SPECIAL_FIRST_DRAFT — NOT the latest draft. We
    #     filter that out below and fall back to `latest_draft_phabricator_version`,
    #     which correctly returns the latest NORMAL_DRAFT.
    query = """
query PhabricatorDiffChangesetsQuery($query_params: [PhabricatorDiffQueryParams!]!) {
  query: phabricator_diff_query(query_params: $query_params) {
    results {
      nodes {
        number
        phabricator_versions(orderby: created_time_reverse, first: 1) {
          nodes {
            number
            version_status
            phabricator_version_changesets {
              nodes {
                fbid: phabricator_version_fbid
                filepath_hash
                filename
              }
            }
          }
        }
        latest_draft_phabricator_version {
          number
          phabricator_version_changesets {
            nodes {
              fbid: phabricator_version_fbid
              filepath_hash
              filename
            }
          }
        }
      }
    }
  }
}
"""

    variables = {"query_params": [{"numbers": [diff_number]}]}

    result = run_jf_graphql(query, variables)

    try:
        node = result["query"][0]["results"]["nodes"][0]
        candidates = _candidate_versions(node)
        if not candidates:
            raise RuntimeError("Failed to query changesets")
        return candidates
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Failed to query changesets")


def get_first_changeset(diff_number: str) -> tuple[str, str]:
    """Return (filename, composite_id) for the first file in the diff's latest
    version.

    Used to anchor a "top-level" comment to a real changeset line when no working
    top-level-comment API path exists (see top_level_comment.py).
    """
    for version in _fetch_candidates(diff_number):
        for changeset in _changesets_of(version):
            filename = changeset.get("filename")
            fbid = changeset.get("fbid")
            filepath_hash = changeset.get("filepath_hash")
            if (
                isinstance(filename, str)
                and isinstance(fbid, str)
                and isinstance(filepath_hash, str)
            ):
                return filename, f"{fbid}|{filepath_hash}"
    raise RuntimeError(f"No changeset files found in D{diff_number}")


def get_changeset_composite_id(diff_number: str, file_path: str) -> str:
    candidates = _fetch_candidates(diff_number)

    # Search candidates in preference order (published first, then draft).
    # Falling back to the draft is necessary for files that only exist in the
    # draft (e.g., a new file added after the last publish) — the published
    # version doesn't contain the file at all, so anchoring there is impossible.
    seen_filenames: list[str] = []
    for version in candidates:
        for changeset in _changesets_of(version):
            filename = changeset.get("filename")
            if isinstance(filename, str):
                seen_filenames.append(filename)
            if filename != file_path:
                continue
            fbid = changeset.get("fbid")
            filepath_hash = changeset.get("filepath_hash")
            if not isinstance(fbid, str) or not isinstance(filepath_hash, str):
                raise RuntimeError(
                    f"Malformed changeset for '{file_path}' in D{diff_number}: "
                    f"fbid={fbid!r}, filepath_hash={filepath_hash!r}"
                )
            return f"{fbid}|{filepath_hash}"

    available = sorted(set(seen_filenames))
    raise ValueError(
        f"File '{file_path}' not found in D{diff_number}. Available files: {', '.join(available)}"
    )


def submit_review_action(diff_number: str) -> None:
    diff_num = diff_number.lstrip("D")
    mutation = """
mutation UpdatePhabricatorDiffMutation($input: UpdatePhabricatorDiffData!) {
  update_phabricator_diff(data: $input) {
    client_mutation_id
  }
}
"""
    variables = {
        "input": {
            "number": diff_num,
            "action": "none",
            "attach_inlines": True,
            "client_caller": "diff_comments_skill",
        }
    }
    try:
        run_jf_graphql(mutation, variables)
        print(f"success Submitted review action on D{diff_num}")
    except RuntimeError as e:
        print("Error: Failed to submit review action", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(1)


def add_inline_comment(
    diff_number: str,
    file_path: str,
    line_number: int,
    end_line: int | None = None,
    is_old_file: bool = False,
    intent: CommentIntent | None = None,
    message: str | None = None,
    draft: bool = False,
    ai_signature: bool = False,
    suggested: str | None = None,
    submit: bool = False,
) -> None:
    diff_num = diff_number.lstrip("D")

    line_length = 1 if end_line is None else (end_line - line_number + 1)

    composite_id = get_changeset_composite_id(diff_num, file_path)

    variables = {
        "input": {
            "comparison_changeset_dst_fbid": composite_id,
            "is_new_file": not is_old_file,
            "line_number": line_number,
            "line_length": line_length - 1,
            # When --submit, create as draft so attach_inlines can pick it up
            "skip_draft": not draft and not submit,
            "client_caller": "diff_comments_skill",
        }
    }

    if intent:
        variables["input"]["comment_intent"] = intent.value

    if message:
        # Skip AI signature for drafts since they'll be reviewed before publishing
        use_signature = ai_signature and not draft
        variables["input"]["content"] = add_ai_attribution(message, use_signature)

    if suggested is not None:
        variables["input"]["suggested"] = suggested

    mutation = """
mutation InlineDraftCreateMutation($input: DifferentialInlineDraftCreateData!) {
  differential_inline_draft_create(data: $input) {
    comment {
      id
    }
  }
}
"""

    try:
        run_jf_graphql(mutation, variables)
        if draft:
            comment_type = "draft inline comment"
        elif suggested is not None:
            comment_type = "inline comment with suggested edit"
        else:
            comment_type = "inline comment"
        print(
            f"success Added {comment_type} to D{diff_num} on {file_path}:{line_number}"
        )
        if submit:
            submit_review_action(diff_num)
    except RuntimeError as e:
        print("Error: Failed to create inline comment", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add an inline comment to a Phabricator diff"
    )
    parser.add_argument("diff_number", help="Diff number (e.g., D12345)")
    parser.add_argument("file_path", help="File path in the diff")
    parser.add_argument("line_number", type=int, help="Line number to comment on")
    parser.add_argument(
        "end_line",
        type=int,
        nargs="?",
        help="End line number (for multi-line comments)",
    )
    parser.add_argument(
        "--old",
        action="store_true",
        dest="is_old_file",
        help="Comment on old version of file",
    )
    parser.add_argument(
        "--intent",
        choices=[intent.value for intent in CommentIntent],
        help="Comment intent (must be uppercase)",
    )
    message_group = parser.add_mutually_exclusive_group(required=True)
    message_group.add_argument("--message", help="Comment text")
    message_group.add_argument(
        "--message-file", help="Path to file containing comment text"
    )
    parser.add_argument(
        "--suggested",
        help="Suggested replacement code for the commented lines",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Keep comment as draft (don't publish immediately)",
    )
    parser.add_argument(
        "--ai-signature",
        action="store_true",
        help="Append 'Sent from Claude Code' signature to the comment",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Publish inline comment and submit a review action",
    )

    parser.add_argument(
        "--suggested-file",
        help="Path to file containing suggested replacement code",
    )

    args = parser.parse_args()
    if args.draft and args.submit:
        parser.error("--draft and --submit are mutually exclusive")
    if args.message_file and not os.path.isfile(args.message_file):
        parser.error(f"--message-file path does not exist: {args.message_file}")
    if args.suggested and args.suggested_file:
        parser.error("--suggested and --suggested-file are mutually exclusive")
    if args.suggested_file and not os.path.isfile(args.suggested_file):
        parser.error(f"--suggested-file path does not exist: {args.suggested_file}")
    return args


def main() -> None:
    args = parse_args()

    message = args.message
    if args.message_file:
        with open(args.message_file) as f:
            message = f.read()

    intent = CommentIntent(args.intent) if args.intent else None

    suggested = args.suggested
    if args.suggested_file:
        with open(args.suggested_file) as f:
            suggested = f.read()

    add_inline_comment(
        args.diff_number,
        args.file_path,
        args.line_number,
        args.end_line,
        args.is_old_file,
        intent,
        message,
        args.draft,
        args.ai_signature,
        suggested,
        args.submit,
    )


if __name__ == "__main__":
    main()
