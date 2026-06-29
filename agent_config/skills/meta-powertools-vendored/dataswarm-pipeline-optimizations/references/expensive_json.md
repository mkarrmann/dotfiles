### Pattern Description

Applies to engines: Spark and Presto

Parsing the same VARCHAR column several times in the same query is inefficient
since it requires the engine to do unnecessary repeated computation. This is
particularly important if the number of repeated operations is significant
enough.

See note: https://fb.workplace.com/notes/137892702349756

### Example

Working with JSON will most likely involve working with a string representation
of an array or a kind of mapping/object, so the following two are examples of
working with JSON arrays in Presto and mappings in Spark.

In Presto:

```sql
SELECT
    CAST(JSON_PARSE(col) AS ARRAY<VARCHAR>)[1],
    CAST(JSON_PARSE(col) AS ARRAY<VARCHAR>)[2],
    CAST(JSON_PARSE(col) AS ARRAY<VARCHAR>)[3],
    CAST(JSON_PARSE(col) AS ARRAY<VARCHAR>)[4],
    CAST(JSON_PARSE(col) AS ARRAY<VARCHAR>)[5],
    CAST(JSON_PARSE(col) AS ARRAY<VARCHAR>)[6]
FROM table
```

In Spark:

```sql
SELECT
    JSON_EXTRACT_SCALAR(col, '$.foo') AS foo,
    JSON_EXTRACT_SCALAR(col, '$.bar') AS bar,
    FROM_JSON(col, 'a INT, b DOUBLE').a + FROM_JSON(config, 'a INT, b DOUBLE').b,
    CAST(FROM_JSON(col, 'a INT, b DOUBLE').a AS VARCHAR),
    CAST(FROM_JSON(col, 'a INT, b DOUBLE').b AS VARCHAR)
FROM table
```

### How to Fix This Pattern

The suggested fix is to extract all of the common parses into a subquery or a
Common Table Expression (CTE).

For the Presto example:

```sql
SELECT
    col[1],
    col[2],
    col[3],
    col[4],
    col[5],
    col[6]
FROM (
    SELECT
        CAST(JSON_PARSE(col) AS ARRAY<VARCHAR>) AS col
    FROM table
)

-- OR

WITH table_col_parsed AS (
    SELECT
        CAST(JSON_PARSE(col) AS ARRAY<VARCHAR>) AS col
    FROM table
)
SELECT
    col[1],
    col[2],
    col[3],
    col[4],
    col[5],
    col[6]
FROM table_col_parsed
```

For the Spark example:

```sql
SELECT
    col.foo AS foo,
    col.bar AS bar,
    col.a + config.b,
    CAST(col.a AS VARCHAR),
    CAST(col.b AS VARCHAR)
FROM (
    SELECT
        FROM_JSON(col, 'a INT, b DOUBLE, foo VARCHAR, bar VARCHAR') AS col
    FROM table
)

-- OR

WITH table_col_parsed AS (
    SELECT
        FROM_JSON(col, 'a INT, b DOUBLE, foo VARCHAR, bar VARCHAR') AS col
    FROM table
)
SELECT
    col.foo AS foo,
    col.bar AS bar,
    col.a + config.b,
    CAST(col.a AS VARCHAR),
    CAST(col.b AS VARCHAR)
FROM table_col_parsed
```

### How Do We Detect This Pattern

We use UPM to analyze the AST and count how many times a particular column from
a table is parsed by any JSON parsing function available in both Presto and
Spark.

In Presto we look for the following:

- `CAST(JSON_PASE(x) AS MAP/ARRAY<...>)`
- `JSON_EXTRACT(x, ...)`
- `JSON_EXTRACT_SCALAR(x, ...)`
- `JSON_SIZE(x)`
- `JSON_ARRAY_CONTAINS(x, ...)`
- `JSON_ARRAY_LENGTH(x)`
- `JSON_ARRAY_GET(x, ...)`

In Spark we look for:

- `FROM_JSON(x, ...)`
- `GET_JSON_OBJECT(x, ...)`
- `FB_JSON_AS_MAP(x)`
- `FB_JSON_AS_ARRAY(x)`
- `JSON_TUPLE(x, ...)`
- `FB_JSON_PATH_EXTRACTOR(x, ...)`
- `FB_JSON_KEYS(x)`
- `FB_JSON_ARRAY_GET_OBJ(x, ...)`
- `JSON_EXTRACT_SCALAR(x, ...)`

If any of the columns is involved in a number of JSON parsing functions >= a
defined threshold, a suggestion to fix the query is triggered.

This threshold is currently set to 6, so any query with one or more of the
previous functions being applied to the same column at least 6 times will be
flagged.
