# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

"""
Update an existing DaiQuery saved query with new SQL by creating a new version.

This is a minimal counterpart to fetch_query.py for the update path.
The skill SKILL.md documents this command but the actual script was missing.
"""

import argparse
import json
import sys

from daiquery.daiquerycli.daiqueryapi import DaiqueryApi
from facebook.daiquery.daiquery.thrift_types import (
    Query as TQuery,
    QueryConfig as TQueryConfig,
)


class UpdateQueryError(Exception):
    """Raised for any user-visible failure during update_query."""


def update_query(
    query_id: int,
    sql: str,
    new_name: str | None = None,
) -> dict[str, object]:
    """
    Load an existing query by id, replace its SQL (and optionally name), and
    publish a new version.

    NOTE: TQueryConfig and TQuery are thrift-python read-only structs — direct
    setattr on `report.sql` / `report.name` (which Report defines as property
    setters that internally call `setattr(self._config, 'sql', value)`) raises
    AttributeError on thrift's `_make_readonly_mutate_attr`. The supported
    pattern is to construct a new TQueryConfig / TQuery with the desired field
    overridden and reassign report._config / report._query. There is no
    higher-level public Report API for this; daiquerycli's own `push_update`
    flow does the same thing internally (helpers.py line ~301).
    """
    with DaiqueryApi() as api:
        try:
            report = api.report_by_id(query_id)
        except Exception as exc:
            raise UpdateQueryError(
                f"Could not load query {query_id}. The id may not exist, or you "
                f"may not have permission to read it. Underlying error: {exc}"
            ) from exc

        old_cfg = report._config
        # TQueryConfig is immutable; rebuild with new SQL, forwarding every
        # other field so we don't silently erase server-side state (e.g.
        # `purposes`, which gates downstream tooling).
        report._config = TQueryConfig(
            sql=sql,
            default_dateid=old_cfg.default_dateid,
            namespace_name=old_cfg.namespace_name,
            schema=old_cfg.schema,
            tier=old_cfg.tier,
            macros=old_cfg.macros,
            purposes=old_cfg.purposes,
        )
        if new_name is not None:
            old_q = report._query
            # Same rebuild pattern for TQuery: forward every field so we don't
            # detach Unidash tabs or clobber server-managed timestamps.
            report._query = TQuery(
                query_id=old_q.query_id,
                container_id=old_q.container_id,
                name=new_name,
                description=old_q.description,
                create_time=old_q.create_time,
                modify_time=old_q.modify_time,
                starred=old_q.starred,
                unidash_tabs=old_q.unidash_tabs,
            )

        try:
            updated = api.update_report(report)
        except Exception as exc:
            raise UpdateQueryError(
                f"Failed to publish a new version for query {query_id}. The "
                f"update may have been rejected by validation, or the user may "
                f"lack write permission on the workspace. Underlying error: {exc}"
            ) from exc

        workspace_id = updated._query.container_id
        return {
            "success": True,
            "query_id": query_id,
            "workspace_id": workspace_id,
            "url": (
                "https://www.internalfb.com/intern/daiquery/workspace/"
                f"{workspace_id}/{query_id}/"
            ),
            "query_name": updated._query.name,
            "version_id": updated._version.version_id,
        }


def _read_sql(args: argparse.Namespace) -> str:
    """Resolve --sql / --sql-file with explicit error messages."""
    if args.sql is not None:
        return args.sql
    path = args.sql_file
    try:
        with open(path) as fh:
            return fh.read()
    except FileNotFoundError as exc:
        raise UpdateQueryError(f"--sql-file not found: {path}") from exc
    except PermissionError as exc:
        raise UpdateQueryError(f"--sql-file is not readable: {path}") from exc
    except UnicodeDecodeError as exc:
        raise UpdateQueryError(
            f"--sql-file is not valid UTF-8: {path} ({exc})"
        ) from exc
    except OSError as exc:
        raise UpdateQueryError(f"Could not read --sql-file {path}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update an existing DaiQuery saved query with new SQL."
    )
    parser.add_argument(
        "--query-id", "-q", type=int, required=True, help="DaiQuery query ID"
    )
    sql_group = parser.add_mutually_exclusive_group(required=True)
    sql_group.add_argument(
        "--sql", "-s", type=str, default=None, help="Inline SQL content"
    )
    sql_group.add_argument(
        "--sql-file",
        "-f",
        type=str,
        default=None,
        help="Path to a file containing the new SQL",
    )
    parser.add_argument(
        "--name", "-n", type=str, default=None, help="Optional new name for the query"
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    args = parser.parse_args()

    try:
        sql = _read_sql(args)
        result = update_query(args.query_id, sql, args.name)
    except UpdateQueryError as exc:
        if args.format == "json":
            print(
                json.dumps(
                    {"success": False, "query_id": args.query_id, "error": str(exc)},
                    indent=2,
                )
            )
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"Updated query {result['query_id']}")
        print(f"  Name:        {result['query_name']}")
        print(f"  Workspace:   {result['workspace_id']}")
        print(f"  Version ID:  {result['version_id']}")
        print(f"  URL:         {result['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
