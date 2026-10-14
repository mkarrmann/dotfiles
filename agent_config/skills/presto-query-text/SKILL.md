---
name: presto-query-text
description: >-
  Use when you need the SQL text of a past Presto query: looking it up by query
  id (e.g. `20261009_174500_70538_s9izr`), or searching for queries whose text
  mentions a table, column, function, or literal. Covers `meta presto.query
  metadata --section=sql`, the column ACL on `di.presto_query_statistics.query`
  vs the masking `di.presto_query_statistics_view`, partition/retention/lag
  limits, and why Scuba `presto_queries` cannot be used for query text. Trigger
  keywords: query text, query sql, presto query id, find queries that use,
  who queries this table, presto_query_statistics, presto_query_statistics_view,
  "<masked due to security>", "<Query text not sent to Scuba>".
---

# Presto query text

Pick the source by what you start from:

| You have | Use |
|---|---|
| A query id | `meta presto.query metadata --section=sql` |
| A substring of the SQL (table, UDF, literal) | `di.presto_query_statistics_view`, partition-bounded |
| A user / source / time window, no text | Scuba `presto_queries` to find ids, then look each one up by id |

## By query id

```bash
meta presto.query metadata --query-id=20261009_174500_70538_s9izr --section=sql -o json
```

The text is in `query_sql`, and `url` links to the Presto query UI page for
the same query. Drop `--section` for timings, resources, and errors, or use
`--section=all`.

- A query is not visible for the first few minutes after it finishes. During
  that window you get `No query info found for the requested query_id`; retry
  later instead of treating it as nonexistent.
- Lookups of ~90-day-old ids work, which matches the Hive retention below.
- It returned the unmasked text of a `security`-schema query for Matt, whose
  access is likely elevated (Presto team). Whether it masks for other callers
  is unverified.

## Searching by text

Search the view, not the base table, and always bound **both** partition
columns:

```bash
meta --timeout 300 presto.query execute --namespace=di --timeout-seconds=270 \
  --reasoning='<why>' -o csv --sql="
SELECT query_id, user, source, schema_name, query_state
FROM di.presto_query_statistics_view
WHERE ds = '2026-10-09'
  AND ts = '2026-10-09+15:00:99'
  AND strpos(query, 'my_table_name') > 0
LIMIT 20"
```

- Partitions are hourly: `ds=YYYY-MM-DD`, `ts=YYYY-MM-DD+HH:00:99`. The `ts`
  hour matches the timestamp prefix of the query ids in it. The newest
  partition trails wall time by about an hour.
- One `ts` partition searched with `strpos` finishes well inside the CLI's
  limit. A whole `ds` or a multi-day range does not, because the request ends
  after 120s by default. Scan an hour at a time, or add selective predicates
  (`user`, `source`, `query_type`, `environment`) first.
- Retention: the oldest partition was `ds=2026-07-12` on 2026-10-09 (~90 days),
  despite the table's nominal `retention_days`. To check the current limit:
  `meta hive.table partitions --namespace=di --table=presto_query_statistics --limit=1`.
- Select `query_id` and look up the full text by id rather than pulling `query`
  for many rows. Texts can be very large.

### Base table vs view

`di.presto_query_statistics.query` is a protected column. Its comment says:

> This is a protected column and can be accessed for allowed queries using the
> view `presto_query_statistics_view`

Most callers are denied that column on the base table. Every other column is
readable there.

`di.presto_query_statistics_view` is a `SECURITY DEFINER` view (owner
`grp:presto`) over `presto_query_statistics_unfiltered_view`, so it returns the
text to anyone who can read the view, except for some sessions:

```sql
IF(((NOT (schema_name LIKE 'libra%'))
    AND (NOT (schema_name IN ('security', 'workplace', 'hr', 'finance', 'legal', 'legal2')))),
   query, '<masked due to security>') query
```

The mask keys on the session's default `schema_name`, not on the tables the
query reads. The view also adds derived columns: `has_where_clause`,
`query_limit`, `has_order_by`, `has_group_by`. To check the current mask:
`SHOW CREATE VIEW di.presto_query_statistics_view`.

The masking exists to protect sensitive query text. Do not try to get around
it, for example by fetching masked queries one at a time by id to collect
their text.

## Scuba `presto_queries` has no query text

The `query` column exists and is filterable, but it holds `''` or
`'<Query text not sent to Scuba>'`, so `CONTAINS(query, ...)` silently matches
nothing. Use the dataset only to find query ids by attributes. Its oldest row
was 2026-07-23 on 2026-10-09 (~78 days):

```bash
meta scuba.dataset sql --dataset=presto_queries --reasoning='<why>' -o csv --sql="
SELECT \`query_id\`, \`user\`, \`source\`, \`schema\`
FROM presto_queries
WHERE \`time\` >= NOW()-3600 AND \`user\` = 'someuser'
LIMIT 20"
```

Then look up each id with `meta presto.query metadata`.
