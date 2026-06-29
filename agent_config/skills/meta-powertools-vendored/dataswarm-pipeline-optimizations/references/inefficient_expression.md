### Pattern Description

Applies to engines: Spark and Presto

There are several sub-categories that qualify as inefficient expressions:

- Calculating latest DS instead of using macro
- Too many CASE WHEN statements
- Too many nested IF statements
- Too many sibling IF statements
- Redundant CASTs
- Redundant ORDER BY

---

1. **Calculating latest ds instead of using the macro**. We already have a macro
   for '<LATEST_DS:$table_name>', so there is no need to have an expensive query
   to scan all partitions for it.

E.g.,

```
SELECT *
FROM table
WHERE ds = (
    SELECT max(ds) FROM table
)
```

can be replaced with

```
SELECT *
FROM table
WHERE ds = '<LATEST_DS:table>'
```

## <br/>

<br/>
2. **Too many CASE WHEN statements**
If a CASE statement has too many WHEN conditions, these are analyzed sequentially per row and can be expensive to compute.

E.g.,

```
CASE
      WHEN condition_1 THEN 1
      ...
      WHEN condition_1000 THEN 1000
      ELSE 0
END
```

In this situation, the order of the CASE WHEN statements matter, so try to put
the most common scenario first if possible.

Try to reduce the number of possible conditions by filtering the rows first.

Try to break up a large daisy-chain by utilizing IF statments. E.g.,

```
IF(first_check,
  CASE WHEN foo THEN 1 WHEN bar THEN 2 ... END,
  CASE WHEN baz THEN 3 WHEN qux THEN 4 ... END
)
```

Look for any conditions that are mutually exclusive, perhaps they can be better
handled with a UNION of 2 queries.

## <br/>

<br/>
3. **Too many Nested IF statements**
If there are too many nested IF statements, these are analyzed sequentially per row and can be expensive to compute.

E.g.,

```
IF(condition_1, 1,
  IF(condition_2, 2,
        ....
                 IF(condition_n, n, 0)
))
```

In this situation, the order of IF statements matter, so try to put the most
common scenario first if possible.

Try to reduce the number of possible conditions by filtering the rows first.

Try to break up a large daisy-chain by utilizing IF statments. E.g.,

```
IF(first_check,
  IF(condition_1, 1, IF(condition_2, 2, ...)),
  IF(condiiton_500, 500, IF(condition_501, 501, ...))
)
```

Look for any conditions that are mutually exclusive, perhaps they can be better
handled with a UNION of 2 queries.

## <br/>

<br/>
4. **Too many Sibling IF statements**
If there are too many sibling IF statements, these are analyzed per row and perhaps they can be combined to have fewer of them.

E.g.,

```
SELECT
IF(condition_1, 1, 0) AS is_condition_1,
IF(condition_2, 1, 0) AS is_condition_2,
...
IF(condition_n, 1, 0) AS is_condition_n
FROM
...
```

In this situation, make sure that all of these aliases/cols are actually used.

If some of them are optional, try to store the result in a MAP or ARRAY of ROWs,
and perform some pre-computation to only calculate the ones needed.

## <br/>

<br/>

5. **Redundant CASTs** This occurs when there's a binary expression and the
   original type of both operands are already compatible, so there's no need to
   cast each one. E.g., Assume x and y are of type BIGINT.

```
CAST(x AS VARCHAR) = CAST(y AS VARCHAR)
CAST(x AS VARCHAR) > CAST(y AS VARCHAR)
CAST(ds AS VARCHAR) = CAST('<DATEID>' AS VARCHAR)
```

To fix this, simply remove the CAST from both sides.

Note: For frameworks that generate dynamic code, you have to be more careful
since it may be operating on columns for which the type is not known ahead of
time.

## <br/>

<br/>
6. **Redundant ORDER BY**
This occurs when there's a subquery with an ORDER BY statement that is not needed because there syntax tree does not have a function that is sensitive to order, such as a LIMIT.

For instance, all of these have a redundant ORDER BY

```
-- Redundant since root scope does not care about the ORDER BY of the subquery
SELECT a FROM (
  SELECT a, COUNT(*) AS num
  FROM table
  WHERE ds = '<DATEID-1>'
  GROUP BY 1
  ORDER BY 2 DESC
)
```

\_

```
SELECT * FROM (
  SELECT a, COUNT(*) AS num
  FROM table
  WHERE ds = '<DATEID-1>'
  GROUP BY 1
  -- Redundant since does not have a LIMIT
  ORDER BY 2 ASC
)
ORDER BY num DESC
LIMIT 10
```

\_

```
-- Semantically equivalent to previous example
WITH subquery AS (
  SELECT a, COUNT(*) AS num
  FROM table
  WHERE ds = <DATEID-1>
  GROUP BY 1
  -- Redundant since does not have a LIMIT
  ORDER BY 2 ASC
)
SELECT *
FROM subquery
ORDER BY num DESC
LIMIT 10
```

\_

```
Incorrect:
SELECT ARRAY_AGG(a)
FROM (
  SELECT a, COUNT(*) AS num
  FROM table
  WHERE ds = DATEID-1
  GROUP BY 1
  -- Redundant, should be re-written as below.
  ORDER BY 2 DESC
)

Correct:
SELECT ARRAY_AGG(a ORDER BY num DESC)
FROM (
  SELECT a, COUNT(*) AS num
  FROM table
  WHERE ds = '<DATEID-1>'
  GROUP BY 1
)
```

The same applies to other functions such as ROW_NUMBER and RANK.

There are several sub-categories that qualify as inefficient expressions:

- `COUNT(DISTINCT ...)` should be replaced by `APPROX_DISTINCT(...)` for Presto
  and `FB_APPROX_DISTINCT(...)` for Spark
- `ARRAY_DISTINCT(ARRAY_AGG(...))` or `ARRAY_AGG(DISTINCT col)` may be replaced
  by either
  - `FILTER(SET_AGG(...), x -> x IS NOT NULL)` if nulls are disallowed, matching
    the default behavior of `ARRAY_AGG(DISTINCT())`
  - `SET_AGG(...)` if nulls are allowed to be present.

- `ARRAY_DISTINCT(FLATTEN(ARRAY_AGG(...)))` or
  `ARRAY_DISTINCT(ARRAY_SORT(FLATTEN(ARRAY_AGG(...))))` should be replaced by
  `SET_UNION(...)`

---

## **APPROX_DISTINCT(x)**

is preferable over `COUNT(DISTINCT(x))` since the former is much faster but
slightly less accurate. The suggested function returns the approximate number of
distinct input values. Zero is returned if all input values are null.

This function should produce a standard error of no more than _e_, which is the
standard deviation of the (approximately normal) error distribution over all
possible sets. It does not guarantee an upper bound on the error for any
specific input set. The current implementation of this function requires that e
be in the range of [0.0040625, 0.26000]

E.g.,

```
SELECT COUNT(DISTINCT(x))
FROM table
```

can be replaced with the following for Presto

```
SELECT APPROX_DISTINCT(x)
FROM table
```

<br/>
and this for Spark,
```
SELECT FB_APPROX_DISTINCT(x)
FROM table
```
<br/>
---
<br/>
## **SET_AGG(x)**

is a more efficient implementation and returns an array created from the
distinct input x elements, which allows NULL to be present. This is a change in
behavior from ARRAY_DISTINCT(ARRAY_AGG(...)) which does not contain NULL values.

E.g.,

```
SELECT ARRAY_DISTINCT(ARRAY_AGG(x))
FROM table
```

or

```
SELECT ARRAY_AGG(DISTINCT x)
FROM table
```

can be replaced with

```
SELECT SET_AGG(x)
FROM table
```

## <br/>

<br/>
## **SET_UNION(x)**

is a more efficient implementation returns an array of all the distinct values
contained in each array of the input, which allows NULL to be present. This is a
change in behavior from ARRAY_DISTINCT(FLATTEN(ARRAY_AGG(...))) which does not
contain NULL values. `set_union(array(T)) -> array(T)`

E.g.,

```
SELECT ARRAY_DISTINCT(FLATTEN(ARRAY_AGG(x)))
FROM table
```

or

```
SELECT ARRAY_DISTINCT(ARRAY_SORT(FLATTEN(ARRAY_AGG(x))))
FROM table
```

may be replaced with

```
SELECT SET_UNION(x)
FROM table
```

To exclude `NULLS`

```
SELECT FILTER(SET_UNION(x), x -> x IS NOT NULL)
FROM table
```
