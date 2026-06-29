#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

"""Common utilities for diff-comments scripts."""

import json
import subprocess
from enum import Enum
from typing import Any


class CommentIntent(Enum):
    """Valid comment intent values for Phabricator diff comments."""

    NIT = "NIT"
    BLOCKING = "BLOCKING"
    ASIDE = "ASIDE"
    CLARIFY = "CLARIFY"
    CODE_STYLE = "CODE_STYLE"
    CONTEXT = "CONTEXT"


# Standard postfix for AI-generated comments
AI_COMMENT_POSTFIX = "\n\n_Sent from Claude Code_"


def add_ai_attribution(message: str, enabled: bool = False) -> str:
    """Add AI attribution postfix to a message if enabled.

    Args:
        message: The original message text
        enabled: Whether to add the AI signature postfix

    Returns:
        Message with AI attribution postfix if enabled, otherwise unchanged
    """
    if enabled:
        return f"{message}{AI_COMMENT_POSTFIX}"
    return message


def run_jf_graphql(query: str, variables: dict[str, Any]) -> Any:
    """Run jf graphql command and return parsed JSON result.

    Args:
        query: GraphQL query string
        variables: Dictionary of variables for the query

    Returns:
        Parsed JSON response from GraphQL

    Raises:
        RuntimeError: If the GraphQL command fails
    """
    cmd = ["jf", "graphql", "--query", query, "--variables", json.dumps(variables)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"GraphQL command failed: {result.stderr}")
    return json.loads(result.stdout)


def get_diff_fbid(diff_number: str) -> str:
    """Resolve a diff number to its internal FBID.

    Args:
        diff_number: Diff number without D prefix (e.g., "12345")

    Returns:
        The diff's FBID string

    Raises:
        RuntimeError: If the diff is not found
    """
    query = """
query GetDiffFbid($number: String!) {
  phabricator_diff(number: $number) {
    id
  }
}
"""
    result = run_jf_graphql(query, {"number": diff_number})
    diff_data = result.get("phabricator_diff")
    if not diff_data:
        raise RuntimeError(f"Diff D{diff_number} not found")
    return str(diff_data["id"])
