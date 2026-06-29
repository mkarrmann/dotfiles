**LINT Check enabled in DataWorkbench and DataCastle**

### Pattern Description

Applies to engines: Spark There are 3 sub-categories that qualify as
**inefficient functions** when using Spark.

1. **Multiple Map Concat**: Creating a large map by creating many singleton maps
   and concatenating them instead of creating big one right away.
   E.g.`FB_MAP_CONCAT(MAP(k1, v1), MAP(k2, v2), FB_MAP_CONCAT(MAP(k3, v3), MAP(k4, v4)))`this
   can be replaced with `MAP(k1, v1, k2, v2, k3, v3, k4, v4)`
2. **Top-N Collect**: Getting top K elements by first collecting all and then
   picking K. E.g.,`FB_MAP_TOP_N(FB_COLLECT_MAP(a, b), 3)`this can be replaced
   with `FB_TOP_N_MAP(a, b, 3)`
3. **Small Function**: Using FB_JAVA_F for short code snippets that likely
   already exist and are built-in, which can allow the execution engines to make
   more optimizations instead of running arbitrary code.There can literally be
   hundreds of examples, so we illustrate some simple one here of things that
   can be done E.g.,

```
FB_JAVA_F('x > 0', true, 'Integer x')
```

=> Can be done in Spark SQL easily: `x > 0`

```
FB_JAVA_F('x != null', true, 'Integer x')
```

=> Can be done in Spark SQL easily: `x IS NOT NULL`

```
FB_JAVA_F('x.toLowerCase().matches(".*somestring.*")', true, 'String x')
```

=> Can be done in Spark SQL easily: `LOWER(x) LIKE '%somestring%'`

```
FB_JAVA_F('return x << 1', 1L, 'Long x')
```

=> Can be done in Spark SQL easily: `shiftleft(x, 1)`

```
FB_MAP_KEY_FILTER_F(my_features, FB_JAVA_F('k < 2147483648L && k > 0', TRUE, 'Long k'))
```

=> Can now be better done using native "map_filter" as of Spark 3.0 for serious
savings: \*New\* Spark 3.0 solution:

```
MAP_FILTER(my_features, (k, v) -> k < 2147483648L AND k > 0)
```

Older Spark 2.4 workaround:

```
WITH input AS (
    SELECT
        MAP(-1, 1, 0, 2, 1, 3, 2147483647L, 4, 2147483648L, 5) my_features,
        NAMED_STRUCT('lower_bound', 0, 'upper_bound', 2147483648L) AS params
    FROM dim_one_row
),
filter_keys AS (
    SELECT
        my_features,
        FILTER(
            ZIP_WITH(
                MAP_KEYS(my_features),
                MAP_VALUES(my_features),
                (k, v) -> (k, v)
            ),
            p -> p.k < params.upper_bound AND p.k > params.lower_bound
        ) filtered
    FROM input
)
SELECT
    my_features,
    FB_MAKE_MAP(TRANSFORM(filtered, p -> p.k), TRANSFORM(filtered, p -> p.v)) my_features_filtered
FROM filter_keys;
```

These can all be replaced by built-in functions in SQL or by UDFs (user-defined
functions). It is better to avoid UDF in favor of SparkSQL functions as it
avoids deserialization of data so that it can process in Scala and then
serialize it again. Furthermore, Spark-SQL functions undergo a lot of testing
and are likely to provide better performance. Lastly, it allows the query engine
to perform optimizations like predicate push-down instead of executing arbitrary
JVM code.

### How to Fix This Pattern

- Try to simplify the code by removing small snippets of FB_JAVA_F with built-in
  functions or UDFs.
- Become familiar with the UDFs that already exist at FB
  https://www.internalfb.com/intern/udf (make sure to filter for Spark)
- If you are using FB_JAVA_F to help filter maps using FB_MAP_KEY_FILTER_F or
  FB_MAP_VALUE_FILTER_F, try this unique
  [approach](https://fb.workplace.com/groups/spark.users/permalink/2824145037854280/)
  as a workaround using ZIP_WITH, a struct, and TRANSFORM.

### How Do We Detect This Pattern

We use regex to parse the SQL query and extract all code snippets inside of
`FB_JAVA_F('$code', $default_value, $type)`.If the code snippet is less than 80
characters long, we consider it as something that MOST LIKELY can be replaced
with built-in functions or a UDF.For this reason, the accuracy confidence of
this pattern is around 90-95%. **EXAMPLE DIFFS**
[D27739674](/intern/diff/27739674/)

#### Pattern Detection in DataWorkbenCh and DataCastLe

This pattern is being used in linter to analyze pipelines in DataWorkbench and
DataCastle. In DataWorkbench, user can see inline warnings(or from Tester Run
output) on pipelines that contains this pattern. In DataCastle, if such pattern
is detected in a diff, a warning message will be displayed in the **Dataswarm**
signal section. For details, please refer to
https://www.internalfb.com/intern/wiki/PAX/PAX_Linting/.
