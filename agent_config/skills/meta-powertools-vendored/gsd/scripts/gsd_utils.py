#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
# pyre-strict

"""Common utilities for GSD scripts."""

import json
import subprocess
from typing import Any


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


def get_current_user_fbid() -> str:
    """Get the current user's FBID via GraphQL.

    Returns:
        Current user's FBID as a string

    Raises:
        RuntimeError: If unable to determine user FBID
    """
    query = "query { me { id } }"
    result = run_jf_graphql(query, {})
    fbid = result["me"]["id"]

    if not fbid:
        raise RuntimeError("Could not determine user FBID")

    return fbid


def get_actor_id() -> str:
    """Get the current user's actor ID for mutations.

    Returns:
        Actor ID as a string

    Raises:
        RuntimeError: If unable to determine actor ID
    """
    query = "query { viewer { actor { id } } }"
    result = run_jf_graphql(query, {})
    actor_id = result["viewer"]["actor"]["id"]

    if not actor_id:
        raise RuntimeError("Could not determine actor ID")

    return actor_id


def resolve_task_number_to_fbid(task_number: str) -> str:
    """Resolve a task number (e.g. '255798207' or 'T255798207') to its FBID.

    The internal_task_edit mutation requires FBIDs, not task numbers.

    Args:
        task_number: Task number with or without 'T' prefix

    Returns:
        Task FBID as a string

    Raises:
        RuntimeError: If unable to resolve task number
    """
    num = task_number.lstrip("Tt")
    query = f"query {{ task(number: {num}) {{ id }} }}"
    result = run_jf_graphql(query, {})
    fbid = result["task"]["id"]

    if not fbid:
        raise RuntimeError(f"Could not resolve task T{num} to FBID")

    return fbid
