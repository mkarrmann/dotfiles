#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
# pyre-strict

"""Move a task from one GSD project to another."""

import argparse
import json
import sys
from typing import Any

from .gsd_utils import get_actor_id, resolve_task_number_to_fbid, run_jf_graphql


def move_task_to_project(
    task_id: str,
    from_project: str,
    to_project: str,
) -> dict[str, Any]:
    """Move a task between GSD projects.

    Args:
        task_id: Task number (without 'T' prefix)
        from_project: Source project FBID
        to_project: Destination project FBID

    Returns:
        Dictionary with success status and task details
    """
    task_num = task_id.lstrip("Tt")
    task_fbid = resolve_task_number_to_fbid(task_num)
    actor_id = get_actor_id()

    mutation = """
    mutation MoveTaskBetweenGSDProjects(
        $actor_id: ID!,
        $task_ids: [ID!]!,
        $remove_from_gsd_plannable_ids: [ID!]!,
        $add_to_gsd_plannable_ids: [ID!]!
    ) {
        internal_task_edit(data: {
            actor_id: $actor_id,
            task_ids: $task_ids,
            remove_from_gsd_plannable_ids: $remove_from_gsd_plannable_ids,
            add_to_gsd_plannable_ids: $add_to_gsd_plannable_ids
        }) {
            tasks {
                id
                task_number
            }
        }
    }
    """

    variables = {
        "actor_id": actor_id,
        "task_ids": [task_fbid],
        "remove_from_gsd_plannable_ids": [from_project],
        "add_to_gsd_plannable_ids": [to_project],
    }

    run_jf_graphql(mutation, variables)

    return {
        "success": True,
        "task_number": f"T{task_num}",
        "from_project": from_project,
        "to_project": to_project,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Move a task between GSD projects",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --task-id T123456 --from-project 4251442578437015 --to-project 4251442578999999
""",
    )

    parser.add_argument("--task-id", required=True, help="Task number")
    parser.add_argument("--from-project", required=True, help="Source project FBID")
    parser.add_argument("--to-project", required=True, help="Destination project FBID")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = move_task_to_project(
            task_id=args.task_id,
            from_project=args.from_project,
            to_project=args.to_project,
        )

        print(json.dumps(result, indent=2))

    except RuntimeError as e:
        error_result = {
            "success": False,
            "task_number": args.task_id,
            "from_project": args.from_project,
            "to_project": args.to_project,
            "error": str(e),
        }
        print(json.dumps(error_result, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
