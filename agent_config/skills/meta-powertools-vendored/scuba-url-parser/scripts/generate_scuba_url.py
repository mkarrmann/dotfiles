#!/usr/bin/env python3
# pyre-strict
"""
Scuba URL Generator - Creates Scuba UI URLs from query components

This script generates Scuba query URLs that can be opened in a browser.
It's the inverse operation of parse_scuba_url.py.

Features:
- Build URLs from individual query components (dimensions, derived columns, filters)
- Optionally shorten URLs using fburl
- Support for all derived column types (Normal, Aggregated, String)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote


@dataclass
class DerivedColumn:
    """Represents a derived column in a Scuba query."""

    name: str
    sql: str
    col_type: str = "Aggregated"  # "Normal", "Aggregated", or "String"
    is_used: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "isUsed": self.is_used,
            "name": self.name,
            "sql": self.sql,
            "type": self.col_type,
        }


@dataclass
class Constraint:
    """Represents a filter constraint in a Scuba query.

    Uses Scuba UI's drillstate format with 'column' and 'value' keys.
    Values are wrapped in a list for the drillstate format.
    """

    column: str
    op: str  # "eq", "neq", "substr", "!substr", "gt", "lt", "gte", "lte", "regeq"
    value: str | list[str]  # Single value or list, NOT wrapped/encoded
    is_included_in_query: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization.

        Uses Scuba UI's expected keys: 'column', 'value'.

        The Scuba UI drillstate format wraps values as a JSON-encoded array
        string inside the value list. For example:
        - Single value ``"foo"`` becomes ``["[\\"foo\\"]"]``
        - Multiple values ``["a", "b"]`` becomes ``["[\\"a\\",\\"b\\"]"]``

        This matches the exact format the Scuba UI produces when you save
        a query with filters.
        """
        if isinstance(self.value, list):
            vals = self.value
        else:
            vals = [self.value]
        # Encode as JSON array string to match Scuba UI drillstate format
        val_list = [json.dumps(vals, separators=(",", ":"))]
        return {
            "column": self.column,
            "op": self.op,
            "value": val_list,
            "is_included_in_query": self.is_included_in_query,
        }


@dataclass
class JoinConfig:
    """Represents a JOIN configuration between two Scuba tables.

    Used by ScubaJoinQueryBuilder to encode the dataset parameter as a JSON
    object instead of a plain table name.
    """

    table1: str
    table2: str
    table1_join_column: str
    table2_join_column: str
    join_type: str = "INNER"
    table1_is_snapshot: bool = False
    table2_is_snapshot: bool = False
    joins_option: str = "ALL"
    join_sql: str = ""
    table1_alias: str | None = None
    table2_alias: str | None = None

    def to_dataset_dict(self) -> dict[str, Any]:
        """Produce the camelCase JSON object for the URL ``dataset`` parameter.

        All fields are included (even when empty/null) to match the format
        the Scuba UI expects.  Key names follow the exact casing used by
        the Scuba frontend (notably ``joinSQL`` with uppercase SQL).
        """
        return {
            "table1": self.table1,
            "table2": self.table2,
            "table1JoinColumn": self.table1_join_column,
            "table2JoinColumn": self.table2_join_column,
            "table1IsSnapshot": self.table1_is_snapshot,
            "table2IsSnapshot": self.table2_is_snapshot,
            "joinType": self.join_type,
            "joinsOption": self.joins_option,
            "joinSQL": self.join_sql,
            "table1Alias": self.table1_alias,
            "table2Alias": self.table2_alias,
        }


@dataclass
class ScubaQueryBuilder:
    """Builder for constructing Scuba query URLs."""

    dataset: str
    start: str = "-7 days"
    end: str = "now"
    dimensions: list[str] = field(default_factory=list)
    derived_cols: list[DerivedColumn] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    top: int = 100
    order: str = "weight"
    order_desc: bool = True
    pool: str = "uber"
    view: str = "table_client"
    metric: str = "count"
    timezone: str = "America/Los_Angeles"

    def add_dimension(self, dimension: str) -> "ScubaQueryBuilder":
        """Add a dimension to group by."""
        self.dimensions.append(dimension)
        return self

    def add_derived_column(
        self,
        name: str,
        sql: str,
        col_type: str = "Aggregated",
    ) -> "ScubaQueryBuilder":
        """
        Add a derived column.

        Args:
            name: Column alias name
            sql: SQL expression for the column
            col_type: Column type - one of:
                - "Aggregated": For aggregate functions (SUM, COUNT, AVG, etc.)
                - "Normal": For row-level expressions (CASE, IF, etc.) that should
                            be included in GROUP BY
                - "String": For string expressions that don't aggregate
        """
        self.derived_cols.append(DerivedColumn(name=name, sql=sql, col_type=col_type))
        return self

    def add_constraint(
        self,
        column: str,
        op: str,
        value: str | list[str],
    ) -> "ScubaQueryBuilder":
        """
        Add a filter constraint.

        Args:
            column: Column name to filter on
            op: Operator - one of:
                - "eq": equals
                - "neq": not equals
                - "substr": contains substring
                - "!substr": does not contain substring
                - "gt", "lt", "gte", "lte": comparison operators
                - "regeq": regex match
            value: Value(s) to filter by (single string or list of strings)
        """
        # Store value as-is - no encoding or wrapping
        # The Constraint.to_dict() method will serialize it correctly
        if isinstance(value, list):
            # Make a copy to avoid mutation issues
            stored_value: str | list[str] = list(value)
        else:
            stored_value = value
        self.constraints.append(Constraint(column=column, op=op, value=stored_value))
        return self

    def build_drillstate(self) -> dict[str, Any]:
        """Build the drillstate JSON object."""
        # Collect all dimensions including Normal derived columns
        all_dimensions = list(self.dimensions)
        for col in self.derived_cols:
            if col.col_type == "Normal" and col.name not in all_dimensions:
                all_dimensions.append(col.name)

        drillstate: dict[str, Any] = {
            "purposes": [],
            "end": self.end,
            "start": self.start,
            "filterMode": "DEFAULT",
            "modifiers": [],
            "sampleCols": [],
            "cols": [],
            "derivedCols": [col.to_dict() for col in self.derived_cols],
            "mappedCols": [],
            "enumCols": [],
            "return_remainder": False,
            "should_pivot": False,
            "is_timeseries": self.view == "time_view",
            "hideEmptyColumns": False,
            "timezone": self.timezone,
            "compare": [],
            "compare_mode": "normal",
            "samplingRatio": "1",
            "minBucketSamples": 0,
            "top": str(self.top),
            "time_bucket": "fine",
            "bucket": "1",
            "dimensions": all_dimensions,
            "metric": self.metric,
            "aggregateList": [],
            "param_dimensions": [],
            "order": self.order,
            "order_desc": self.order_desc,
            "constraints": [[c.to_dict() for c in self.constraints]],
            "c_constraints": [[]],
            "b_constraints": [[]],
            "ignoreGroupByInComparison": False,
        }
        return drillstate

    def build_url(self) -> str:
        """Build the full Scuba query URL."""
        drillstate = self.build_drillstate()
        encoded_drillstate = quote(json.dumps(drillstate))
        encoded_pool = quote(self.pool, safe="")
        url = (
            f"https://www.internalfb.com/intern/scuba/query/"
            f"?dataset={self.dataset}"
            f"&drillstate={encoded_drillstate}"
            f"&pool={encoded_pool}"
            f"&view={self.view}"
        )
        return url


@dataclass
class ScubaJoinQueryBuilder(ScubaQueryBuilder):
    """Builder for constructing Scuba JOIN query URLs between two tables.

    The ``dataset`` field holds the table1 name (for compatibility with the
    parent class), while ``join_config`` holds the full join specification.
    """

    join_config: JoinConfig | None = None

    @classmethod
    def create(
        cls,
        table1: str,
        table2: str,
        join_column1: str,
        join_column2: str,
        join_type: str = "INNER",
        **kwargs: Any,
    ) -> "ScubaJoinQueryBuilder":
        """Factory method to create a JOIN query builder.

        Args:
            table1: First table name
            table2: Second table to join with
            join_column1: Join column in table1
            join_column2: Join column in table2
            join_type: JOIN type (INNER, LEFT, RIGHT, FULL)
            **kwargs: Additional keyword arguments passed to the builder
        """
        join_config = JoinConfig(
            table1=table1,
            table2=table2,
            table1_join_column=join_column1,
            table2_join_column=join_column2,
            join_type=join_type,
        )
        return cls(
            dataset=table1,
            pool=f"join:{kwargs.pop('pool', 'uber')}",
            join_config=join_config,
            **kwargs,
        )

    def build_url(self) -> str:
        """Build the full Scuba JOIN query URL.

        Overrides the parent to encode ``dataset`` as a JSON object instead
        of a plain string.  Both the dataset JSON and pool value are
        percent-encoded so that URL shorteners (fburl) do not double-encode
        them.
        """
        drillstate = self.build_drillstate()
        encoded_drillstate = quote(json.dumps(drillstate))
        if self.join_config is not None:
            dataset_value = quote(
                json.dumps(self.join_config.to_dataset_dict(), separators=(",", ":")),
                safe="",
            )
        else:
            dataset_value = self.dataset
        encoded_pool = quote(self.pool, safe="")
        url = (
            f"https://www.internalfb.com/intern/scuba/query/"
            f"?dataset={dataset_value}"
            f"&drillstate={encoded_drillstate}"
            f"&pool={encoded_pool}"
            f"&view={self.view}"
        )
        return url


def create_request_size_bucket_query(
    dataset: str = "mgp_data_service_app_logs",
    time_range: str = "-7 days",
    data_solution_filter: str | None = "P2P",
    tw_task_handle_filter: str | None = None,
) -> ScubaQueryBuilder:
    """
    Create a query with request size bucketing for analyzing timeout/error rates.

    This is a pre-built query template for analyzing request performance by size.

    Args:
        dataset: Scuba dataset name
        time_range: Time range (e.g., "-7 days", "-24 hours")
        data_solution_filter: Filter by data_solution (e.g., "P2P")
        tw_task_handle_filter: Filter by tw_task_handle substring

    Returns:
        ScubaQueryBuilder configured with request size bucket analysis
    """
    builder = ScubaQueryBuilder(dataset=dataset, start=time_range)

    # Add dimensions
    builder.add_dimension("data_solution")
    builder.add_dimension("llm_model_id")

    # Add request size bucket as a Normal (non-aggregated) derived column
    # This will be included in GROUP BY
    builder.add_derived_column(
        name="request_size_bucket",
        sql=(
            "CASE "
            "WHEN LENGTH(serialized_data_solution_request) < 2000 THEN 'Small (<2K)' "
            "WHEN LENGTH(serialized_data_solution_request) < 5000 THEN 'Medium (2K-5K)' "
            "WHEN LENGTH(serialized_data_solution_request) < 10000 THEN 'Large (5K-10K)' "
            "WHEN LENGTH(serialized_data_solution_request) < 100000 THEN 'XLarge (10K-100K)' "
            "ELSE 'XXLarge (100K+)' "
            "END"
        ),
        col_type="Normal",  # Non-aggregated, will be in GROUP BY
    )

    # Add aggregated metrics
    builder.add_derived_column(
        name="timeout_errors",
        sql="SUM(CASE WHEN error_type = 'TimeoutError' THEN 1 ELSE 0 END)",
        col_type="Aggregated",
    )

    builder.add_derived_column(
        name="exception_rate_pct",
        sql=(
            "(SUM(CASE WHEN error_type = 'TimeoutError' THEN 1 ELSE 0 END) "
            "* 100.0 / COUNT(*))"
        ),
        col_type="Aggregated",
    )

    builder.add_derived_column(
        name="app_server_success_rate",
        sql=(
            "CAST_AS_DOUBLE(SUM(IF(error_type is not null AND error_type not in "
            "('MGPInvalidInputException', 'LLMClientException', 'LLMServerException'), "
            "0, 1))) / CAST_AS_DOUBLE(COUNT(*))"
        ),
        col_type="Aggregated",
    )

    builder.add_derived_column(
        name="llm_client_success_rate",
        sql=(
            "CAST_AS_DOUBLE(SUM(IF(error_type is not null AND "
            "error_type != 'LLMClientException', 0, 1))) / CAST_AS_DOUBLE(COUNT(*))"
        ),
        col_type="Aggregated",
    )

    builder.add_derived_column(
        name="llm_server_success_rate",
        sql=(
            "CAST_AS_DOUBLE(SUM(IF(error_type is not null AND "
            "error_type != 'LLMServerException', 0, 1))) / CAST_AS_DOUBLE(COUNT(*))"
        ),
        col_type="Aggregated",
    )

    # Add filters
    if data_solution_filter:
        builder.add_constraint("data_solution", "eq", data_solution_filter)

    if tw_task_handle_filter:
        builder.add_constraint("tw_task_handle", "substr", tw_task_handle_filter)

    return builder


def _build_template_query(args: argparse.Namespace) -> ScubaQueryBuilder:
    """Build a query using a pre-built template."""
    data_solution_filter = None
    tw_task_handle_filter = None

    for f in args.filter:
        if f.startswith("data_solution="):
            parts = f.split("=", 1)[1].split(":", 1)
            data_solution_filter = parts[1] if len(parts) > 1 else parts[0]
        elif f.startswith("tw_task_handle="):
            parts = f.split("=", 1)[1].split(":", 1)
            tw_task_handle_filter = parts[1] if len(parts) > 1 else parts[0]

    builder = create_request_size_bucket_query(
        dataset=args.dataset,
        time_range=args.time_range,
        data_solution_filter=data_solution_filter,
        tw_task_handle_filter=tw_task_handle_filter,
    )
    builder.view = args.view
    return builder


def _parse_derived_column(col_spec: str) -> tuple[str, str, str]:
    """Parse a derived column specification.

    Returns:
        Tuple of (name, sql, col_type)

    Raises:
        ValueError: If the format is invalid
    """
    parts = col_spec.split(":", 2)
    if len(parts) < 2:
        raise ValueError(
            f"Invalid derived column format: {col_spec}. "
            "Expected format: 'name:sql:type' or 'name:sql'"
        )
    name = parts[0]
    sql = parts[1]
    col_type = parts[2] if len(parts) > 2 else "Aggregated"
    return name, sql, col_type


def _parse_filter(filter_spec: str) -> tuple[str, str, str | list[str]]:
    """Parse a filter specification.

    Returns:
        Tuple of (column, op, value) where value may be a list for multi-value
        filters using comma separation (e.g. ``col=eq:val1,val2``).

    Raises:
        ValueError: If the format is invalid
    """
    if "=" not in filter_spec:
        raise ValueError(
            f"Invalid filter format: {filter_spec}. Expected format: 'column=op:value'"
        )
    column, rest = filter_spec.split("=", 1)
    if ":" in rest:
        op, value_str = rest.split(":", 1)
    else:
        op = "eq"
        value_str = rest
    # For operators that support multi-value, split comma-separated values
    if op in ("eq", "neq", "substr", "!substr") and "," in value_str:
        value: str | list[str] = [v.strip() for v in value_str.split(",")]
    else:
        value = value_str
    return column, op, value


def _build_custom_query(args: argparse.Namespace) -> ScubaQueryBuilder:
    """Build a custom query from command-line arguments."""
    if args.join_table:
        builder: ScubaQueryBuilder = ScubaJoinQueryBuilder.create(
            table1=args.dataset,
            table2=args.join_table,
            join_column1=args.join_column1,
            join_column2=args.join_column2,
            join_type=args.join_type,
            start=args.time_range,
            top=args.limit,
            view=args.view,
        )
    else:
        builder = ScubaQueryBuilder(
            dataset=args.dataset,
            start=args.time_range,
            top=args.limit,
            view=args.view,
            pool=args.pool,
        )

    for dim in args.dimension:
        builder.add_dimension(dim)

    for col_spec in args.derived_column:
        name, sql, col_type = _parse_derived_column(col_spec)
        builder.add_derived_column(name, sql, col_type)

    for filter_spec in args.filter:
        column, op, value = _parse_filter(filter_spec)
        builder.add_constraint(column, op, value)

    return builder


def _normalize_view(view: str) -> str:
    """Map friendly time-series view aliases to the Scuba ScubaViewType value.

    Scuba's ScubaViewType expects ``time_view`` for time series; ``timeseries``
    is rejected by the frontend. Accept common aliases and normalize them.
    """
    if view.lower() in ("timeseries", "time_series", "time", "time_view"):
        return "time_view"
    return view


def _output_url(url: str, args: argparse.Namespace) -> None:
    """Output the generated URL, optionally shortening it."""
    if not args.quiet:
        print("Generated Scuba Query URL:")
        print("=" * 60)

    if args.shorten:
        import subprocess

        result = subprocess.run(
            ["fburl", url],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            short_url = result.stdout.strip()
            print(short_url)
            if not args.quiet:
                print("=" * 60)
                print(f"\nFull URL:\n{url}")
        else:
            print(url)
            if not args.quiet:
                print("=" * 60)
                print(f"\nWarning: Could not shorten URL: {result.stderr}")
    else:
        print(url)
        if not args.quiet:
            print("=" * 60)


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Generate Scuba query URLs from query components",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate a request size bucket analysis URL
  python generate_scuba_url.py --template request_size_bucket \\
      --dataset mgp_data_service_app_logs \\
      --filter data_solution=P2P

  # Generate a custom query URL
  python generate_scuba_url.py --dataset my_dataset \\
      --dimension user_id \\
      --dimension country \\
      --derived-column "error_count:SUM(IF(is_error, 1, 0)):Aggregated" \\
      --filter "status=eq:success"

  # Shorten the generated URL
  python generate_scuba_url.py --template request_size_bucket --shorten
        """,
    )

    parser.add_argument(
        "--dataset",
        "-d",
        required=True,
        help="Scuba dataset name",
    )
    parser.add_argument(
        "--template",
        "-t",
        choices=["request_size_bucket"],
        help="Use a pre-built query template",
    )
    parser.add_argument(
        "--time-range",
        default="-7 days",
        help="Time range (e.g., '-7 days', '-24 hours'). Default: -7 days",
    )
    parser.add_argument(
        "--dimension",
        action="append",
        default=[],
        help="Add a dimension to group by (can be used multiple times)",
    )
    parser.add_argument(
        "--derived-column",
        action="append",
        default=[],
        help=(
            "Add a derived column in format 'name:sql:type' where type is "
            "'Aggregated', 'Normal', or 'String'. Example: "
            "'error_count:SUM(IF(is_error, 1, 0)):Aggregated'"
        ),
    )
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        help=(
            "Add a filter in format 'column=op:value'. "
            "Operators: eq, neq, substr, !substr, gt, lt, gte, lte, regeq. "
            "Example: 'status=eq:success' or 'name=substr:test'"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Result limit. Default: 100",
    )
    parser.add_argument(
        "--shorten",
        action="store_true",
        help="Shorten the URL using fburl (requires fburl CLI)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only output the URL, no other information",
    )
    parser.add_argument(
        "--view",
        default="table_client",
        help=(
            "Scuba view type. Common values: table_client, Samples, "
            "time_view (time series). Aliases 'timeseries'/'time_series' are "
            "normalized to 'time_view'. Default: table_client"
        ),
    )
    parser.add_argument(
        "--join-table",
        default=None,
        help=(
            "Second table to join with. When specified, --dataset is table1. "
            "Requires --join-column1 and --join-column2."
        ),
    )
    parser.add_argument(
        "--join-column1",
        default=None,
        help="Join column in table1 (the --dataset table)",
    )
    parser.add_argument(
        "--join-column2",
        default=None,
        help="Join column in table2 (the --join-table table)",
    )
    parser.add_argument(
        "--join-type",
        default="INNER",
        choices=["INNER", "LEFT", "RIGHT", "FULL"],
        help="JOIN type. Default: INNER",
    )
    parser.add_argument(
        "--pool",
        default="uber",
        help=(
            "Query pool. Default: uber. For Hive-backed Scuba datasets, pass "
            "presto:<hive_namespace> (e.g. presto:videos) to query the "
            "underlying Presto table — look up the namespace via "
            "`meta presto.table info --table=<X>`. Note: `presto` alone "
            "(no namespace) errors with `rockfort_express.presto.root not "
            "found`. Ignored when --join-table is set (the join builder "
            "auto-prefixes with `join:`)."
        ),
    )

    args = parser.parse_args()

    # Normalize view aliases (e.g. "timeseries"/"time_series" -> "time_view")
    args.view = _normalize_view(args.view)

    # Validate JOIN arguments
    if args.join_table:
        if not args.join_column1 or not args.join_column2:
            parser.error(
                "--join-column1 and --join-column2 are required "
                "when --join-table is specified"
            )

    try:
        # Build the query using template or custom configuration
        if args.template == "request_size_bucket":
            builder = _build_template_query(args)
        else:
            builder = _build_custom_query(args)

        # Generate and output URL
        url = builder.build_url()
        _output_url(url, args)

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error generating URL: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
