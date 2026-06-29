### Pattern Description

Applies to engines: Spark and Presto

When aggregations happen on keys which don't contain any duplicates or almost no
duplicates, we incur unneccessary cost of shuffling/sorting the data. Engines
can try to perform partial aggregation (aggregate locally before shuffle) which
can also add cost if it's not reducing amount of data much.

### Example

A duplicate-checking query

```sql
SELECT
    user_id
FROM my_table
GROUP BY user_id
HAVING count(*) > 1
```

If `user_id` is unique in my_table, having a group by is not changing the output
of the query, but it adds cost to it. Either get rid of the query, or decide to
remove aggregation in this case, if you're just needing a list of deduped
userids. Or, if `user_id` is not unique but still is very high cardinality, then
removing partial aggregation is advised, unless data is skewed on user_id.

### How to Fix This Pattern

This pattern comes in two flavors:

- Ineffective **partial** aggregations
  - Try skipping partial aggregation if your data is not very skewed and high
    cardinality.
    - Spark: Add `spark_opts={"spark.sql.fb.only.partial.agg.enabled": "false"}`
      in your Spark operator.
    - Presto: Add
      `session={'prefer_partial_aggregation': 'false', 'optimize_hash_generation': 'false'}`
      in your Presto operator.
  - Choice whether or not to use partial aggregation will NOT affect results in
    any way
  - Note: Since choice of whether to use partial aggregation or not happens on
    query level, if there are other aggregations in the query which benefit from
    partial aggregation these could be affected negatively, so verify perf
    impact of these config changes.
  - Note: If data is very skewed, removing partial aggregation could cause OOM
- Ineffective **final** aggregations
  - _For Spark_: Verify if keys which are being aggregated on have any
    duplicates (since query stats data we rely on for detection is approximate,
    we can't tell for sure if there were no or very few duplicates)
  - _For Presto_: Stats are accurate and will tell you exactly by how much rows
    did data reduce after aggregation.
  - If there are no duplicates, remove `GROUP BY`
  - If there are very few duplicates, consider whether data can be cleaned up
    upstream or whether deduping it is neccessary

### Which group by was detected as ineffective

If your query has multiple group by-s, or you want to see whether ineffective
aggregate was partial or final one, you can use additional metadata we provide
about this pattern. We don't have a way yet to point to SQL subquery, so for now
this will require a few clicks and basic knowledge about how to interpret query
plans. Go [here](https://fburl.com/unidash/hu086wh5), fill in your pipeline
name, and look at `task_to_properties` column. For each task in your pipeline
which had ineffective aggregate it will have more data about where it happened:

- _For Presto_: There will be `in` and `out` values, these represent how many
  rows were coming into aggregation and how many rows came out of it, there will
  also be `aggregate` which will contain either `PARTIAL` or `FINAL`. You'll
  also see chronos job instance id you can go to, and get to the right Presto
  query UI from there (links in the beginning of chronos job instance log). Open
  "Query Plan" view, in there you can look for aggregate which has the matching
  number of input and output rows.
- _For Spark_: You'll also be able to see number of input and output rows and
  navigate to chronos log. There will be `operatorString` which represents a
  line from Execution plan where ineffective aggregate happened, and in
  functions it will have `partial_{}` if it's partial aggregation or not for
  final aggregation.

### How Do We Detect This Pattern

We look at all group by-s which happen in Dataswarm production workload, and
detect when number of rows coming out of group by is close to the number of rows
coming in.

### Example Fixes

1. D31875691, where partial aggregation step was skipped for many tasks, because
   average cardinality for selected was very high, saving 90%.
2. D31325104, removed group by altogether, because no rows were being deduped.
3. D33029994, where a GROUPING SET operation was optimized by first grouping on
   the largest set of keys
