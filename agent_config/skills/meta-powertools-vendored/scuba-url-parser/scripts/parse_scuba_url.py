#!/usr/bin/env python3
# pyre-strict
"""
Scuba URL Parser - Converts Scuba query URLs to executable SQL

This script parses Scuba query URLs and generates valid SQL that can be
executed using scuba-cli.

NOTE: This script expects full Scuba query URLs. If you have a shortened URL,
it must be expanded before being passed to this script.

IMPORTANT: This script generates correct Scuba SQL syntax:
- Uses APPROX_PERCENTILE() instead of PERCENTILE()
- Uses APPROX_COUNT_DISTINCT() instead of COUNT(DISTINCT)
- Converts percentile aggregations (p50, p75, p95, p99) to APPROX_PERCENTILE
- Handles time expressions properly with now() function
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


@dataclass
class DerivedColumn:
    """Represents a derived column in a Scuba query."""

    name: str
    sql: str
    type: str
    is_used: bool


@dataclass
class JoinConfig:
    """Represents a JOIN configuration between two Scuba tables.

    Scuba JOIN URLs encode the dataset parameter as a JSON object containing
    join configuration instead of a plain table name string.
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JoinConfig":
        """Parse a JOIN config dict from the URL's dataset JSON parameter.

        The URL uses camelCase keys (e.g., ``joinType``, ``joinsOption``).
        The join SQL key is ``joinSQL`` (uppercase SQL) in the Scuba UI.
        Alias fields may be ``null``.
        """
        table1_alias = data.get("table1Alias")
        table2_alias = data.get("table2Alias")
        return cls(
            table1=data.get("table1", ""),
            table2=data.get("table2", ""),
            table1_join_column=data.get("table1JoinColumn", ""),
            table2_join_column=data.get("table2JoinColumn", ""),
            join_type=data.get("joinType", "INNER"),
            table1_is_snapshot=data.get("table1IsSnapshot", False),
            table2_is_snapshot=data.get("table2IsSnapshot", False),
            joins_option=data.get("joinsOption", "ALL"),
            join_sql=data.get("joinSQL", data.get("joinSql", "")),
            table1_alias=table1_alias if isinstance(table1_alias, str) else None,
            table2_alias=table2_alias if isinstance(table2_alias, str) else None,
        )

    def to_sql_from_clause(self) -> str:
        """Produce the SQL FROM clause for a JOIN query.

        Returns something like::

            table1
            INNER JOIN table2 ON table1.col1 = table2.col2
        """
        t1 = self.table1
        t2 = self.table2
        join = self.join_type or "INNER"
        return (
            f"{t1}\n"
            f"{join} JOIN {t2} "
            f"ON {t1}.{self.table1_join_column} = {t2}.{self.table2_join_column}"
        )


@dataclass
class Drillstate:
    """Typed representation of Scuba drillstate JSON."""

    # Time range
    start: str = "-60 minutes"
    end: str = "now"

    # Dimensions
    dimensions: list[str] = field(default_factory=list)

    # Regular columns (non-aggregated)
    cols: list[str] = field(default_factory=list)

    # Derived columns
    derived_cols: list[DerivedColumn] = field(default_factory=list)

    # Show sample columns hint (UI display, not used for SQL generation)
    show_sample_cols: str = ""

    # Sorting & Limiting
    order: str = ""
    order_desc: bool = False
    top: int = 0

    # Constraints (for future implementation)
    constraints: list[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Drillstate":
        """Parse drillstate JSON into typed structure."""
        # Convert derived_cols
        derived_cols = [
            DerivedColumn(
                name=col["name"],
                sql=col["sql"],
                type=col.get("type", ""),
                is_used=col.get("isUsed", False),
            )
            for col in data.get("derivedCols", [])
            if col.get("isUsed", False)
        ]

        # Handle top as either string or int
        top = data.get("top", 0)
        if isinstance(top, str):
            top = int(top) if top.isdigit() else 0

        return cls(
            start=data.get("start", "-60 minutes"),
            end=data.get("end", "now"),
            dimensions=data.get("dimensions", []),
            cols=data.get("cols", []),
            derived_cols=derived_cols,
            show_sample_cols=data.get("show_sample_cols", ""),
            order=data.get("order", ""),
            order_desc=data.get("order_desc", False),
            top=top,
            constraints=data.get("constraints", []),
        )


@dataclass
class ParsedScubaUrl:
    """Represents a parsed Scuba URL with typed components."""

    dataset: str | JoinConfig
    pool: str
    view: str
    drillstate: Drillstate

    @property
    def is_join_query(self) -> bool:
        """Check if this is a JOIN query between two tables."""
        return isinstance(self.dataset, JoinConfig)

    @property
    def dataset_display_name(self) -> str:
        """Human-readable dataset name for display."""
        if isinstance(self.dataset, JoinConfig):
            return f"{self.dataset.table1} JOIN {self.dataset.table2}"
        assert isinstance(self.dataset, str)
        return self.dataset


def parse_scuba_url(url: str) -> ParsedScubaUrl:
    """
    Parse a Scuba query URL and extract its components.

    Args:
        url: The Scuba query URL

    Returns:
        ParsedScubaUrl containing typed components
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    # Extract basic parameters
    dataset_raw = params.get("dataset", [""])[0]
    pool = params.get("pool", ["uber"])[0]
    view = params.get("view", [""])[0]

    # Detect JOIN queries: dataset is a JSON object with a "table1" key
    dataset: str | JoinConfig
    try:
        dataset_obj = json.loads(dataset_raw)
        if isinstance(dataset_obj, dict) and "table1" in dataset_obj:
            dataset = JoinConfig.from_dict(dataset_obj)
        else:
            dataset = dataset_raw
    except (json.JSONDecodeError, ValueError):
        dataset = dataset_raw

    # Extract and decode drillstate
    drillstate_str = params.get("drillstate", ["{}"])[0]
    drillstate_decoded = unquote(drillstate_str)

    drillstate_dict = None

    # Try 1: Standard JSON parsing
    try:
        drillstate_dict = json.loads(drillstate_decoded)
    except json.JSONDecodeError as e:
        print(f"Warning: Invalid JSON at position {e.pos}")
        print(f"Context: ...{drillstate_decoded[max(0, e.pos - 50) : e.pos + 50]}...")

        # Try 2: Fix trailing commas
        print("Trying to fix trailing commas...")
        fixed_json = re.sub(r",(\s*[}\]])", r"\1", drillstate_decoded)
        try:
            drillstate_dict = json.loads(fixed_json)
            print("✓ Successfully parsed after fixing trailing commas")
        except json.JSONDecodeError:
            pass

    # Try 3: Lenient JSON parsing
    if drillstate_dict is None:
        try:
            print("Trying lenient JSON parsing...")
            drillstate_dict = json.loads(drillstate_decoded, strict=False)
            print("✓ Successfully parsed with strict=False")
        except (json.JSONDecodeError, TypeError):
            pass

    # Try 4: Regex fallback extraction
    if drillstate_dict is None:
        print("JSON parsing failed. Using regex fallback to extract key fields...")
        drillstate_dict = extract_from_malformed_json(drillstate_decoded)
        print(f"✓ Extracted fields via regex: {list(drillstate_dict.keys())}")

    # Convert drillstate dict to typed Drillstate object
    drillstate = Drillstate.from_dict(drillstate_dict)

    return ParsedScubaUrl(dataset=dataset, pool=pool, view=view, drillstate=drillstate)


def extract_from_malformed_json(json_str: str) -> dict[str, Any]:
    """
    Extract key fields from malformed JSON using regex as a fallback.

    Args:
        json_str: The malformed JSON string

    Returns:
        Dictionary with extracted fields compatible with Drillstate.from_dict()
    """
    result: dict[str, Any] = {}

    # Extract start time
    start_match = re.search(r'"start"\s*:\s*"([^"]+)"', json_str)
    if start_match:
        result["start"] = start_match.group(1)

    # Extract end time
    end_match = re.search(r'"end"\s*:\s*"([^"]+)"', json_str)
    if end_match:
        result["end"] = end_match.group(1)

    # Extract dimensions array
    dimensions_match = re.search(r'"dimensions"\s*:\s*\[([^\]]+)\]', json_str)
    if dimensions_match:
        dims_str = dimensions_match.group(1)
        # Extract quoted strings from the array
        dims = re.findall(r'"([^"]+)"', dims_str)
        result["dimensions"] = dims
    else:
        result["dimensions"] = []

    # Extract top/limit
    top_match = re.search(r'"top"\s*:\s*(\d+)', json_str)
    if top_match:
        result["top"] = int(top_match.group(1))

    # Extract order column
    order_match = re.search(r'"order"\s*:\s*"([^"]+)"', json_str)
    if order_match:
        result["order"] = order_match.group(1)

    # Extract order_desc
    order_desc_match = re.search(r'"order_desc"\s*:\s*(true|false)', json_str)
    if order_desc_match:
        result["order_desc"] = order_desc_match.group(1) == "true"

    # Extract derived columns (simplified)
    result["derivedCols"] = []
    # Find all derivedCols entries that have isUsed: true
    # Use a more flexible pattern that doesn't depend on key ordering
    derived_cols_section = re.search(
        r'"derivedCols"\s*:\s*\[(.*?)\]', json_str, re.DOTALL
    )
    if derived_cols_section:
        cols_text = derived_cols_section.group(1)
        # Find individual column objects within curly braces
        col_objects = re.findall(r"\{([^{}]*?)\}", cols_text)
        for col_obj in col_objects:
            # Check if this column is used
            is_used_match = re.search(r'"isUsed"\s*:\s*(true|false)', col_obj)
            if is_used_match and is_used_match.group(1) == "true":
                # Extract name and sql independently (order doesn't matter)
                name_match = re.search(r'"name"\s*:\s*"([^"]+)"', col_obj)
                sql_match = re.search(r'"sql"\s*:\s*"([^"]+)"', col_obj)
                type_match = re.search(r'"type"\s*:\s*"([^"]+)"', col_obj)

                if name_match and sql_match:
                    result["derivedCols"].append(
                        {
                            "isUsed": True,
                            "name": name_match.group(1),
                            "sql": sql_match.group(1),
                            "type": type_match.group(1) if type_match else "Aggregated",
                        }
                    )

    # Extract show_sample_cols
    samples_match = re.search(r'"show_sample_cols"\s*:\s*"([^"]+)"', json_str)
    if samples_match:
        result["show_sample_cols"] = samples_match.group(1)

    # Extract constraints (simplified - just try to parse as JSON array)
    constraints_match = re.search(r'"constraints"\s*:\s*\[(.*?)\]', json_str, re.DOTALL)
    if constraints_match:
        try:
            # Try to parse the constraints array as valid JSON
            constraints_json = f"[{constraints_match.group(1)}]"
            result["constraints"] = json.loads(constraints_json)
        except json.JSONDecodeError:
            # If parsing fails, just set empty list
            result["constraints"] = []
    else:
        result["constraints"] = []

    print("Regex extraction summary:")
    print(f"  - Time range: {result.get('start', 'N/A')} to {result.get('end', 'N/A')}")
    print(f"  - Dimensions: {result.get('dimensions', [])}")
    print(f"  - Derived columns: {len(result.get('derivedCols', []))}")
    print(f"  - Constraints: {len(result.get('constraints', []))}")
    print(f"  - Limit: {result.get('top', 'N/A')}")

    return result


def convert_time_expression(time_expr: str) -> str:
    """
    Convert Scuba time expression to SQL format.

    Examples:
        "-10080 minutes" -> "now() - 10080 * 60"
        "-1 minute" -> "now() - 1 * 60"
        "now" -> "now()"
        "2025-11-26 12:44:32" -> "1764189872" (Unix epoch)
    """
    if time_expr == "now":
        return "now()"

    # Parse expressions like "-10080 minutes" or "-1 minute"
    # Handle both singular and plural forms
    if "minute" in time_expr:
        value = time_expr.replace("minutes", "").replace("minute", "").strip()
        # Ensure proper spacing around the operator
        if value.startswith(("+", "-")):
            operator = value[0]
            number = value[1:].strip()
            return f"now() {operator} {number} * 60"
        return f"now() + {value} * 60"
    elif "hour" in time_expr:
        value = time_expr.replace("hours", "").replace("hour", "").strip()
        if value.startswith(("+", "-")):
            operator = value[0]
            number = value[1:].strip()
            return f"now() {operator} {number} * 3600"
        return f"now() + {value} * 3600"
    elif "day" in time_expr:
        value = time_expr.replace("days", "").replace("day", "").strip()
        if value.startswith(("+", "-")):
            operator = value[0]
            number = value[1:].strip()
            return f"now() {operator} {number} * 86400"
        return f"now() + {value} * 86400"

    # Try to parse as datetime string (e.g., "2025-11-26 12:44:32")
    try:
        # Parse the datetime string and convert to Unix timestamp
        dt = datetime.strptime(time_expr, "%Y-%m-%d %H:%M:%S")
        timestamp = int(dt.timestamp())
        return str(timestamp)
    except ValueError:
        # If it doesn't match expected format, return as-is
        pass

    return time_expr


def _is_json_array(val: Any) -> bool:
    """Check if value is a JSON-encoded array string."""
    if not isinstance(val, str):
        return False
    try:
        parsed = json.loads(val)
        return isinstance(parsed, list)
    except (json.JSONDecodeError, ValueError):
        return False


def _escape_sql_string(value: str) -> str:
    """Escape single quotes in SQL string values."""
    return value.replace("'", "''")


def _format_sql_value(val: Any) -> str:
    """
    Format a value for SQL, quoting strings and escaping quotes.

    Note: String values are always quoted, even if they look numeric,
    to preserve type safety (e.g., "123" might be an ID, not a number).
    """
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return f"'{_escape_sql_string(val)}'"
    return str(val)


def _handle_in_operator(col: str, val: Any) -> str | None:
    """Handle IN operator with list of values."""
    if not isinstance(val, list):
        return None
    if all(isinstance(v, str) for v in val):
        values = ", ".join(f"'{_escape_sql_string(v)}'" for v in val)
    else:
        values = ", ".join(str(v) for v in val)
    return f"{col} IN ({values})"


def parse_constraint_to_sql(constraint: dict[str, Any]) -> str | None:
    """
    Convert a Scuba constraint object to SQL WHERE condition.

    Supports both URL constraint formats:
    - {"col": "x", "op": "contains", "val": "y"}
    - {"column": "x", "op": "substr", "value": "y"}

    Also supports ScubaDrillstate canonical operators (substr, eq, neq, etc.)
    and handles NULL values, JSON-encoded arrays, and multiple values.

    Args:
        constraint: Constraint dictionary from drillstate

    Returns:
        SQL WHERE condition string, or None if constraint should be skipped
    """
    # Support both "col" and "column" keys (URL format variations)
    col = constraint.get("col") or constraint.get("column")
    op = constraint.get("op")
    # Support both "val" and "value" keys (URL format variations)
    val = constraint.get("val") or constraint.get("value")

    if not col or not op:
        return None

    # Decode JSON-encoded arrays if present (from ScubaDrillstate internal format)
    if isinstance(val, str) and _is_json_array(val):
        val = json.loads(val)

    # Unwrap single-element lists to scalar values for operators that expect scalars.
    # The Scuba UI always stores values as lists, but operators like substr, gt, lt,
    # regeq etc. expect a single value, not a list.
    if isinstance(val, list) and len(val) == 1:
        val = val[0]

    # Second JSON decode pass: after unwrapping a single-element list, the value
    # may itself be a JSON-encoded array string (e.g. '["active"]' from the Scuba
    # UI drillstate format where to_dict() produces ["\"active\""]).
    if isinstance(val, str) and _is_json_array(val):
        val = json.loads(val)

    # After decoding, unwrap single-element lists again (e.g. ["active"] -> "active")
    if isinstance(val, list) and len(val) == 1:
        val = val[0]

    # Operator normalization map (map URL format -> canonical operators)
    operator_aliases = {
        "contains": "substr",
        "equals": "eq",
        "not_equals": "neq",
        "regex": "regeq",
        "greater_than": "gt",
        "less_than": "lt",
        "greater_equals": "gte",
        "less_equals": "lte",
        "none": "none_contains",
    }

    # Normalize operator to canonical form
    op_canonical = operator_aliases.get(op, op)

    # Handle NULL values
    if val == "null" or val == '"null"' or val is None:
        if op_canonical == "eq":
            return f"{col} IS NULL"
        elif op_canonical == "neq":
            return f"{col} IS NOT NULL"

    # Handle substr (contains) operator
    if op_canonical == "substr":
        escaped_val = _escape_sql_string(str(val))
        return f"strpos({col}, '{escaped_val}') > 0"

    # Handle !substr (not contains) operator
    if op_canonical == "!substr":
        escaped_val = _escape_sql_string(str(val))
        return f"strpos({col}, '{escaped_val}') = 0"

    # Handle eq (equals) operator - supports both single values and arrays
    if op_canonical == "eq" or op == "=":
        if isinstance(val, list):
            # Handle multiple values as IN clause with NULL handling
            non_null_vals = [v for v in val if v not in ("null", '"null"', None)]
            has_null = any(v in ("null", '"null"', None) for v in val)

            conditions = []
            if non_null_vals:
                formatted_vals = ", ".join(_format_sql_value(v) for v in non_null_vals)
                conditions.append(f"{col} IN ({formatted_vals})")
            if has_null:
                conditions.append(f"{col} IS NULL")

            if len(conditions) > 1:
                return f"({' OR '.join(conditions)})"
            elif conditions:
                return conditions[0]
            else:
                return None
        else:
            return f"{col} = {_format_sql_value(val)}"

    # Handle neq (not equals) operator
    if op_canonical == "neq" or op == "!=":
        if isinstance(val, list):
            # Handle multiple values as NOT IN clause with NULL handling
            non_null_vals = [v for v in val if v not in ("null", '"null"', None)]
            has_null = any(v in ("null", '"null"', None) for v in val)

            conditions = []
            if non_null_vals:
                formatted_vals = ", ".join(_format_sql_value(v) for v in non_null_vals)
                conditions.append(f"{col} NOT IN ({formatted_vals})")
            if has_null:
                conditions.append(f"{col} IS NOT NULL")

            if len(conditions) > 1:
                return f"({' AND '.join(conditions)})"
            elif conditions:
                return conditions[0]
            else:
                return None
        else:
            return f"{col} != {_format_sql_value(val)}"

    # Handle regeq (regex match) operator
    if op_canonical == "regeq":
        escaped_val = _escape_sql_string(str(val))
        return f"REGEXP_LIKE({col}, '{escaped_val}')"

    # Handle regneq (regex not match) operator
    if op_canonical == "regneq":
        escaped_val = _escape_sql_string(str(val))
        return f"NOT REGEXP_LIKE({col}, '{escaped_val}')"

    # Handle comparison operators (gt, lt, gte, lte)
    if op_canonical == "gt" or op == ">":
        return f"{col} > {val}"
    if op_canonical == "lt" or op == "<":
        return f"{col} < {val}"
    if op_canonical == "gte" or op == ">=":
        return f"{col} >= {val}"
    if op_canonical == "lte" or op == "<=":
        return f"{col} <= {val}"

    # Handle IN operator (explicit IN, not eq with array)
    if op == "in":
        return _handle_in_operator(col, val)

    # Handle not_empty_string operator (from ScubaDrillstate)
    if op_canonical == "not_empty_string":
        return f"{col} != ''"

    # Handle any_contains operator (array contains any of the values)
    if op_canonical == "any_contains":
        if isinstance(val, list):
            conditions = []
            for v in val:
                escaped_val = _escape_sql_string(str(v))
                conditions.append(f"strpos({col}, '{escaped_val}') > 0")
            return f"({' OR '.join(conditions)})"
        else:
            escaped_val = _escape_sql_string(str(val))
            return f"strpos({col}, '{escaped_val}') > 0"

    # Handle none_contains operator (array contains none of the values)
    if op_canonical == "none_contains":
        if isinstance(val, list):
            conditions = []
            for v in val:
                escaped_val = _escape_sql_string(str(v))
                conditions.append(f"strpos({col}, '{escaped_val}') = 0")
            return f"({' AND '.join(conditions)})"
        else:
            escaped_val = _escape_sql_string(str(val))
            return f"strpos({col}, '{escaped_val}') = 0"

    # Handle regex_any operator (regex match any of the patterns)
    if op_canonical == "regex_any":
        if isinstance(val, list):
            conditions = []
            for v in val:
                escaped_val = _escape_sql_string(str(v))
                conditions.append(f"REGEXP_LIKE({col}, '{escaped_val}')")
            return f"({' OR '.join(conditions)})"
        else:
            escaped_val = _escape_sql_string(str(val))
            return f"REGEXP_LIKE({col}, '{escaped_val}')"

    # Unknown operator
    print(f"Warning: Unknown constraint operator '{op}' for column '{col}'")
    return None


def generate_sql(parsed_data: ParsedScubaUrl) -> str:
    """
    Generate Scuba SQL from parsed URL data.

    NOTE: The pool parameter from parsed_data is NOT included in the generated SQL.
    If you need to specify a pool when executing with scuba, pass it separately:
    scuba --pool uber -e "SELECT ..."

    Args:
        parsed_data: Parsed Scuba URL data with typed drillstate

    Returns:
        SQL query string
    """
    drillstate = parsed_data.drillstate
    dataset = parsed_data.dataset

    # Determine the FROM clause
    if isinstance(dataset, JoinConfig):
        from_clause = dataset.to_sql_from_clause()
    else:
        from_clause = dataset

    # Build SELECT clause
    select_parts = []

    # Add dimensions
    select_parts.extend(drillstate.dimensions)

    # Add regular columns (non-aggregated)
    select_parts.extend(drillstate.cols)

    # Add derived columns
    for col in drillstate.derived_cols:
        select_parts.append(f"{col.sql} AS {col.name}")

    # If no specific columns, use COUNT(*)
    if not select_parts:
        select_parts.append("COUNT(*) AS samples")

    select_clause = ",\n    ".join(select_parts)

    # Build WHERE clause
    where_parts = []

    # Add time range
    start_sql = convert_time_expression(drillstate.start)
    end_sql = convert_time_expression(drillstate.end)
    where_parts.append(f"time > {start_sql}")
    where_parts.append(f"time < {end_sql}")

    # Add constraints (filters)
    # Constraints are stored as nested arrays: [[constraint1, constraint2, ...]]
    # Each element in the outer array is a group of constraints
    for constraint_group in drillstate.constraints:
        if isinstance(constraint_group, list):
            # Handle nested structure: iterate into the group
            for constraint in constraint_group:
                if isinstance(constraint, dict):
                    sql_condition = parse_constraint_to_sql(constraint)
                    if sql_condition:
                        where_parts.append(sql_condition)
        elif isinstance(constraint_group, dict):
            # Handle flat structure for backwards compatibility
            sql_condition = parse_constraint_to_sql(constraint_group)
            if sql_condition:
                where_parts.append(sql_condition)

    where_clause = "\n    AND ".join(where_parts)

    # Build GROUP BY clause
    group_by_clause = ""
    if drillstate.dimensions:
        group_by_clause = f"\nGROUP BY {', '.join(drillstate.dimensions)}"

    # Build ORDER BY clause
    # Only include ORDER BY if the order column exists in the SELECT clause
    # Scuba-specific keywords like 'weight' and 'sampleRatio' are omitted
    order_by_clause = ""
    if drillstate.order:
        # Get all column names from select_parts
        all_columns = set(drillstate.dimensions + drillstate.cols)
        all_columns.update(col.name for col in drillstate.derived_cols)

        # Only add ORDER BY if the order column is in our SELECT
        if drillstate.order in all_columns:
            direction = "DESC" if drillstate.order_desc else "ASC"
            order_by_clause = f"\nORDER BY {drillstate.order} {direction}"
        # Silently omit ORDER BY for Scuba-specific keywords like 'weight', 'sampleRatio'

    # Build LIMIT clause
    limit_clause = ""
    if drillstate.top and (
        isinstance(drillstate.top, int)
        and drillstate.top > 0
        or isinstance(drillstate.top, str)
        and drillstate.top.isdigit()
        and int(drillstate.top) > 0
    ):
        limit_clause = f"\nLIMIT {drillstate.top}"

    # Assemble final SQL
    sql = f"""SELECT
    {select_clause}
FROM {from_clause}
WHERE
    {where_clause}{group_by_clause}{order_by_clause}{limit_clause}"""

    return sql


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Parse Scuba query URLs and generate executable SQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Parse a full Scuba query URL
  python parse_scuba_url.py 'https://www.internalfb.com/intern/scuba/query/?dataset=my_dataset&drillstate=...'

  # Save generated SQL to a file
  python parse_scuba_url.py 'https://www.internalfb.com/intern/scuba/query/?dataset=...' --save query.sql

  # Quiet mode (only output SQL, no other information)
  python parse_scuba_url.py 'https://www.internalfb.com/intern/scuba/query/?dataset=...' --quiet

Note: This script only accepts full Scuba query URLs. Shortened URLs must be expanded separately.
        """,
    )
    parser.add_argument("url", help="Scuba query URL (full URL, not shortened)")
    parser.add_argument(
        "--save", "-s", metavar="FILE", help="Save the generated SQL to a file"
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only output the SQL query, no other information",
    )

    args = parser.parse_args()

    try:
        # Parse the URL
        if not args.quiet:
            print("Parsing Scuba URL...")
        parsed = parse_scuba_url(args.url)

        if not args.quiet:
            print(f"\nDataset: {parsed.dataset_display_name}")
            if isinstance(parsed.dataset, JoinConfig):
                join_cfg = parsed.dataset
                print(f"  Table 1: {join_cfg.table1}")
                print(f"  Table 2: {join_cfg.table2}")
                print(
                    f"  Join: {join_cfg.table1}.{join_cfg.table1_join_column}"
                    f" = {join_cfg.table2}.{join_cfg.table2_join_column}"
                )
                print(f"  Join Type: {join_cfg.join_type}")
            print(f"Pool: {parsed.pool}")
            print(f"View: {parsed.view}")
            print("\nNOTE: The pool is not included in the generated SQL.")
            print(
                f'      If needed, pass it to scuba: scuba --pool {parsed.pool} -e "..."'
            )

            # Display query details
            drillstate = parsed.drillstate
            print(f"\nTime Range: {drillstate.start} to {drillstate.end}")

            if drillstate.dimensions:
                print(f"Dimensions: {', '.join(drillstate.dimensions)}")

            if drillstate.derived_cols:
                print("\nDerived Columns:")
                for col in drillstate.derived_cols:
                    print(f"  - {col.name}: {col.sql}")

            if drillstate.constraints:
                print("\nFilters/Constraints:")
                for constraint in drillstate.constraints:
                    if isinstance(constraint, dict):
                        col = constraint.get("col") or constraint.get("column", "?")
                        op = constraint.get("op", "?")
                        val = constraint.get("val") or constraint.get("value", "?")
                        print(f"  - {col} {op} {val}")

        # Generate SQL
        sql = generate_sql(parsed)

        if not args.quiet:
            print("\n" + "=" * 60)
            print("Generated SQL:")
            print("=" * 60)

        print(sql)

        if not args.quiet:
            print("=" * 60)

            # Show scuba command
            print("\nTo execute with scuba:")
            sql_oneline = sql.replace("\n", " ")
            print(f'scuba -e "{sql_oneline}"')

        # Save SQL to file if requested
        if args.save:
            with open(args.save, "w") as f:
                f.write(sql)
                f.write("\n")
            if not args.quiet:
                print(f"\nSQL saved to: {args.save}")

    except Exception as e:
        print(f"Error parsing URL: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
