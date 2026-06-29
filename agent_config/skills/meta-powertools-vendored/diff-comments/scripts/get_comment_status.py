#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

import argparse
import sys

from diff_comments_utils import run_jf_graphql


def print_comment_status(fbids: list[int]) -> None:
    query = """
query GetCommentStatus($comment_id: ID!) {
  node(id: $comment_id) {
    __typename
    ... on DifferentialTransactionComment {
      id
      content
      message_resolved_status
      line_number
      author {
        name
      }
    }
  }
}
"""

    for fbid in fbids:
        variables = {"comment_id": fbid}

        try:
            result = run_jf_graphql(query, variables)
            node = result.get("node")

            if not node:
                print(f"Comment {fbid} not found", file=sys.stderr)
                continue

            print(f"ID: {node.get('id')}")
            print(f"Content: {node.get('content')}")
            print(f"Line: {node.get('line_number', 'N/A')}")
            print(f"Status: {node.get('message_resolved_status')}")
            print(f"Author: {node.get('author', {}).get('name')}")
            print("---")

        except RuntimeError as e:
            print(f"Error fetching comment {fbid}: {e}", file=sys.stderr)
            sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Get status of comments by FBID")
    parser.add_argument("fbids", type=int, nargs="+", help="Comment FBIDs to query")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        print_comment_status(args.fbids)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
