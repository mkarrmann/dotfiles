#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
# pyre-strict

"""Add tasks to a GSD project."""

import argparse
import json
import sys
from typing import Any

from .gsd_utils import get_actor_id, resolve_task_number_to_fbid, run_jf_graphql


def add_task_to_project(
    task_ids: list[str],
    project_id: str,
) -> dict[str, Any]:
    """Add tasks to a GSD project.

    Args:
        task_ids: List of task numbers (without 'T' prefix)
        project_id: GSD project FBID

    Returns:
        Dictionary with success status and task results
    """
    actor_id = get_actor_id()

    mutation = """
    mutation AddTaskToGSDProject(
        $actor_id: ID!,
        $task_ids: [ID!]!,
        $add_to_gsd_plannable_ids: [ID!]!
    ) {
        internal_task_edit(data: {
            actor_id: $actor_id,
            task_ids: $task_ids,
            add_to_gsd_plannable_ids: $add_to_gsd_plannable_ids
        }) {
            tasks {
                id
                task_number
            }
        }
    }
    """

    success_count = 0
    fail_count = 0
    results = []

    for task_id in task_ids:
        task_num = task_id.lstrip("Tt")

        try:
            task_fbid = resolve_task_number_to_fbid(task_num)

            variables = {
                "actor_id": actor_id,
                "task_ids": [task_fbid],
                "add_to_gsd_plannable_ids": [project_id],
            }

            run_jf_graphql(mutation, variables)
            success_count += 1
            results.append({"task_number": f"T{task_num}", "success": True})
        except RuntimeError as e:
            fail_count += 1
            results.append(
                {
                    "task_number": f"T{task_num}",
                    "success": False,
                    # Use first line only for cleaner JSON output; full error in stderr
                    "error": str(e).split("\n")[0],
                }
            )

    return {
        "success": fail_count == 0,
        "project_id": project_id,
        "total_tasks": len(task_ids),
        "successful": success_count,
        "failed": fail_count,
        "tasks": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add task(s) to a GSD project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --task-ids T123456 --project-id 4251442578437015
  %(prog)s --task-ids T123456 T123457 T123458 --project-id 4251442578437015
""",
    )

    parser.add_argument(
        "--task-ids",
        nargs="+",
        required=True,
        help="Task number(s) - space-separated for multiple tasks",
    )
    parser.add_argument("--project-id", required=True, help="GSD project FBID")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = add_task_to_project(
            task_ids=args.task_ids,
            project_id=args.project_id,
        )

        print(json.dumps(result, indent=2))

        if not result["success"]:
            sys.exit(1)

    except RuntimeError as e:
        error_result = {"success": False, "error": str(e)}
        print(json.dumps(error_result, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
