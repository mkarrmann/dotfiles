# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

"""
Create a new DaiQuery saved query (a classic query referenced by ``query_id``,
NOT a notebook) via the DaiQuery thrift API.

Counterpart to fetch_query.py / update_query.py for the create path. The skill
documents search / fetch / update but had no create command, so creating a
saved query previously required either a hand-written thrift script or the
file-based ``daiquerycli push`` flow. There is no other flag-driven
create-saved-query CLI in fbcode (only one-off scripts that hardcode their
workspace + SQL, and library APIs meant to be imported).

Two modes:

  * Clone an existing query as a template (``--from-query <id>``): inherits the
    template's macros, namespace, schema, and tier; you override the SQL and
    name (and optionally the workspace / namespace / macros). This is the right
    mode when the new query reuses a template's macros -- e.g. the
    ``date_start`` / ``date_end`` bindings a Unidash period picker subscribes
    to.
  * From scratch (no ``--from-query``): pass ``--workspace`` + ``--namespace``
    + ``--name`` + SQL, and optionally ``--macros-json`` for advanced macro
    setups.

Wraps ``DaiqueryApi.create_report`` (``client.createQuery`` +
``client.createQueryVersion``). The thrift structs are read-only, so -- as in
update_query.py -- the config / query / version are built immutably rather than
mutated via setattr.
"""

import argparse
import json
import sys

from daiquery.daiquerycli.daiqueryapi import DaiqueryApi
from daiquery.daiquerycli.helpers import Report
from facebook.daiquery.daiquery.thrift_types import (
    Macro as TMacro,
    Query as TQuery,
    QueryConfig as TQueryConfig,
    QueryVersion as TQueryVersion,
)


# Defaults for a brand-new (non-cloned) query version. A Presto/"completed"
# version is the standard shape for a saved SQL query; clone mode inherits the
# template's values instead.
DEFAULT_VERSION_TYPE = "presto"
DEFAULT_VERSION_STATUS = "completed"


class CreateQueryError(Exception):
    """Raised for any user-visible failure during create_query."""


def _build_macros(macros_json: str | None) -> list[TMacro] | None:
    """Parse ``--macros-json`` (a JSON array of macro objects) into TMacro structs.

    Returns None when the flag is absent so callers can distinguish "no
    override" (inherit the template's macros) from "explicitly no macros"
    (``[]``).
    """
    if macros_json is None:
        return None
    try:
        raw = json.loads(macros_json)
    except json.JSONDecodeError as exc:
        raise CreateQueryError(f"--macros-json is not valid JSON: {exc}") from exc
    if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
        raise CreateQueryError(
            "--macros-json must be a JSON array of objects, e.g. "
            '\'[{"key": "date_start", "value": "<DATEID-1>", "type": "free_text"}]\''
        )
    try:
        return [TMacro(**entry) for entry in raw]
    except TypeError as exc:
        raise CreateQueryError(
            f"--macros-json contains an unknown/invalid macro field: {exc}"
        ) from exc


def create_query(
    name: str,
    sql: str,
    *,
    from_query: int | None = None,
    workspace_id: int | None = None,
    namespace: str | None = None,
    description: str = "",
    macros_json: str | None = None,
    version_type: str | None = None,
) -> dict[str, object]:
    """Create a saved query and return its id / workspace / url / version.

    In clone mode (``from_query`` set) the template's config is inherited and
    only the provided fields are overridden. From scratch, ``workspace_id`` and
    ``namespace`` are required.
    """
    override_macros = _build_macros(macros_json)

    with DaiqueryApi() as api:
        if from_query is not None:
            try:
                template = api.report_by_id(from_query)
            except Exception as exc:
                raise CreateQueryError(
                    f"Could not load template query {from_query}. The id may not "
                    f"exist, or you may lack read permission. Underlying error: {exc}"
                ) from exc
            template_config = template._config
            # Rebuild immutably (TQueryConfig is read-only), forwarding the
            # template's fields and overriding only what the caller specified.
            config = TQueryConfig(
                sql=sql,
                default_dateid=template_config.default_dateid,
                namespace_name=namespace or template_config.namespace_name,
                schema=template_config.schema,
                tier=template_config.tier,
                macros=(
                    override_macros
                    if override_macros is not None
                    else template_config.macros
                ),
            )
            container_id = workspace_id or template._query.container_id
            resolved_version_type = (
                version_type or template._version.type or DEFAULT_VERSION_TYPE
            )
            resolved_version_status = template._version.status or DEFAULT_VERSION_STATUS
        else:
            if workspace_id is None or namespace is None:
                raise CreateQueryError(
                    "Without --from-query you must pass both --workspace and "
                    "--namespace (there is no template to inherit them from)."
                )
            config = TQueryConfig(
                sql=sql,
                namespace_name=namespace,
                macros=override_macros,
            )
            container_id = workspace_id
            resolved_version_type = version_type or DEFAULT_VERSION_TYPE
            resolved_version_status = DEFAULT_VERSION_STATUS

        version = TQueryVersion(
            type=resolved_version_type, status=resolved_version_status
        )
        query = TQuery(
            container_id=container_id,
            name=name,
            description=description,
            starred=False,
        )
        report = Report(query=query, version=version)
        # create_report reads report._config (not report._version.config), so
        # set it directly -- mirrors update_query.py.
        report._config = config

        try:
            created = api.create_report(report)
        except Exception as exc:
            raise CreateQueryError(
                f"Failed to create query in workspace {container_id}. The create "
                f"may have been rejected by validation, or you may lack write "
                f"permission on the workspace. Underlying error: {exc}"
            ) from exc

        new_query_id = created._query.query_id
        new_workspace_id = created._query.container_id
        return {
            "success": True,
            "query_id": new_query_id,
            "workspace_id": new_workspace_id,
            "url": (
                "https://www.internalfb.com/intern/daiquery/workspace/"
                f"{new_workspace_id}/{new_query_id}/"
            ),
            "query_name": created._query.name,
            "version_id": created._version.version_id,
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
        raise CreateQueryError(f"--sql-file not found: {path}") from exc
    except PermissionError as exc:
        raise CreateQueryError(f"--sql-file is not readable: {path}") from exc
    except UnicodeDecodeError as exc:
        raise CreateQueryError(
            f"--sql-file is not valid UTF-8: {path} ({exc})"
        ) from exc
    except OSError as exc:
        raise CreateQueryError(f"Could not read --sql-file {path}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a new DaiQuery saved query (classic query, not a notebook). "
            "Clone a template with --from-query, or build from scratch with "
            "--workspace + --namespace."
        )
    )
    parser.add_argument(
        "--name", "-n", type=str, required=True, help="Name for the new query"
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
        help="Path to a file containing the SQL",
    )
    parser.add_argument(
        "--from-query",
        "-F",
        type=int,
        default=None,
        help=(
            "Template query id to clone macros / namespace / schema / tier from. "
            "Omit to create from scratch (then --workspace and --namespace are "
            "required)."
        ),
    )
    parser.add_argument(
        "--workspace",
        "-w",
        type=int,
        default=None,
        help=(
            "Target workspace/container id. Required without --from-query; "
            "optional override of the template's workspace with --from-query."
        ),
    )
    parser.add_argument(
        "--namespace",
        type=str,
        default=None,
        help=(
            "Data namespace (e.g. 'infrastructure'). Required without "
            "--from-query; optional override with --from-query."
        ),
    )
    parser.add_argument(
        "--description",
        "-d",
        type=str,
        default="",
        help="Optional query description",
    )
    parser.add_argument(
        "--macros-json",
        type=str,
        default=None,
        help=(
            "Optional JSON array of macro objects (thrift Macro fields, e.g. "
            "key/value/type/macro_values). Overrides inherited macros in clone "
            "mode; defines macros from scratch otherwise."
        ),
    )
    parser.add_argument(
        "--version-type",
        type=str,
        default=None,
        help="Override the version data-source type (default 'presto').",
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
        result = create_query(
            args.name,
            sql,
            from_query=args.from_query,
            workspace_id=args.workspace,
            namespace=args.namespace,
            description=args.description,
            macros_json=args.macros_json,
            version_type=args.version_type,
        )
    except CreateQueryError as exc:
        if args.format == "json":
            print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"Created query {result['query_id']}")
        print(f"  Name:        {result['query_name']}")
        print(f"  Workspace:   {result['workspace_id']}")
        print(f"  Version ID:  {result['version_id']}")
        print(f"  URL:         {result['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
