### Pattern Description

Applies to engines: Presto (Spark in development) Detected in queries where at
some point a lot of data gets filtered out (thrown away). This could be
happening while or right after the table was read, when a filter is applied at a
later stage of the query, or after a join.

### How to Fix This Pattern

Depending on what exactly the pipeline is doing, there could be various ways to
improve, and sometimes it's also possible it can't be improved. Some examples:

- If data gets filtered out right after being read, consider modifying the
  schema of the table to make the filter cheaper:
  - use bucketing/partitioning to split the data
  - if filter is based on a value from a complex data type, eg key from a map or
    json, consider extracting it upstream
  - if table is repeatedly read to pick various small parts of it consider
    merging the queries which do it; if sharding the query make column being
    sharded on the partition column
- If data gets filtered out later in the query, is it possible to prepare the
  values being filtered on earlier, or have some less restrictive filter applied
  earlier. For instance, <INPUT> macro expands to (select \* from table where
  partition filters). If you have joins and unnest operations at this stage,
  followed by additional filters that could be moved up, rewrite your queries
  using <INPUT_TABLE_NAME> and <INPUT_WHERE_CLAUSE> macros along with your
  additional filters.
- If same table or subquery is being filtered repeatedly in multiple queries,
  could we use staging table or have these queries merged into one

### where in the query did large data filter happen

You can figure out where in the query did large data filter happen by using
additional metadata we provide about this pattern. We don't have a way yet to
point to SQL subquery, so for now this will require a few clicks and basic
knowledge about how to interpret query plans. Go
[here](https://fburl.com/unidash/hu086wh5), fill in your pipeline name, and look
at `task_to_properties` column. For each task in your pipeline which had large
data filter it will have more data about where it happened:

- _For Presto_: Among other fields, there will be `operation`, which represent
  in which operation did large data filter happen, scan, join or filter.
  Depending on the operation, there will be one or two input row counts and
  output row count, these represent how many rows were coming into the operation
  and how many rows came out of it. You'll also see chronos job instance id you
  can go to, and get to the right Presto query UI from there (links in the
  beginning of chronos job instance log). Open "Query Plan" view, in there you
  can look for particular operator which has the matching number of input and
  output rows.
