#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
# pyre-strict

"""Create a new section within a GSD project."""

import argparse
import json
import sys
from typing import Any

from .gsd_utils import get_current_user_fbid, run_jf_graphql


def create_gsd_section(
    gsd_id: str,
    project_id: str,
    name: str,
) -> dict[str, Any]:
    """Create a new GSD section.

    Args:
        gsd_id: GSD team FBID
        project_id: GSD project FBID
        name: Section name

    Returns:
        Dictionary with section_id, section_name, team_id, and project_id
    """
    mutation = """
    mutation useTasksGSDCreateSectionMutation (
        $actor_id: ID!,
        $section_name: String!,
        $team_id: ID!,
        $project_id: ID!
    ) {
        create_tasks_gsd_section(data: {
            actor_id: $actor_id,
            name: $section_name,
            team: $team_id,
            project: $project_id
        }) {
            tasks_gsd_section {
                id
                name
            }
        }
    }
    """

    actor_id = get_current_user_fbid()
    variables = {
        "actor_id": actor_id,
        "section_name": name,
        "team_id": gsd_id,
        "project_id": project_id,
    }

    result = run_jf_graphql(mutation, variables)

    section_id = result["create_tasks_gsd_section"]["tasks_gsd_section"]["id"]
    section_name = result["create_tasks_gsd_section"]["tasks_gsd_section"]["name"]

    return {
        "success": True,
        "section_id": section_id,
        "section_name": section_name,
        "team_id": gsd_id,
        "project_id": project_id,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new GSD section",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --gsd-id 729330146869874 --project-id 4251442578437015 \\
    --name 'Q1 Deliverables'
""",
    )

    parser.add_argument("--gsd-id", required=True, help="GSD team FBID")
    parser.add_argument("--project-id", required=True, help="GSD project FBID")
    parser.add_argument("--name", required=True, help="Section name")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = create_gsd_section(
            gsd_id=args.gsd_id,
            project_id=args.project_id,
            name=args.name,
        )

        print(json.dumps(result, indent=2))

    except RuntimeError as e:
        error_result = {"success": False, "error": str(e)}
        print(json.dumps(error_result, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
