# Universal Search Query Language Reference

> ⚠️ **This is NOT SQL.** Universal Search uses a Thrift-based query structure. Only documented operations are supported.

## CLI Tool Reference

All queries use the `thriftdbg` CLI tool:

```bash
thriftdbg sendRequest search '<json_payload>' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

> ⚠️ **Keep the command on a single line.** Line continuations (`\`) cause jq parse errors.

## Search Request Structure

Universal Search uses a SQL-like query structure:

```text
SearchRequest {
  select: SelectClause     // What to return
  from: ObjectType         // Which object type (enum value)
  where: WhereClause       // How to filter
  jsonResponseFormat: {}   // Required for JSON output
}
```

## SelectClause Options

| Option | Description | Example |
|--------|-------------|---------|
| `allFields` | Return all fields (**default — use this**) | `{"allFields":{}}` |
| `selectedFields` | Return specific Thrift field paths | `{"selectedFields":["field1","field2"]}` |
| `selectedJsonPaths` | Return specific JSONPath fields | `{"selectedJsonPaths":["$.field1","$.field2"]}` |
| `idOnly` | Return only object IDs | `{"idOnly":{}}` |

**Default to `allFields`** for most queries. This ensures you get the complete object and don't miss relevant fields. Only use `selectedJsonPaths` when:
- Fetching many objects (e.g., all tasks for a large job) where response size matters
- You know exactly which fields you need

**No aggregate functions** (COUNT, SUM, DISTINCT, etc.) - count results client-side.

## ObjectType Enum Values

| Object Type | Value |
|-------------|-------|
| Job | 1 |
| Reservation | 2 |
| Task | 4 |
| Server | 5 |
| ServiceID | 7 |
| TaskUpdateHistoryRecord | 8 |
| Fbpkg | 9 |
| FbpkgVersion | 10 |
| Allowance | 11 |
| ChefShard | 15 |
| NujSpec | 16 |
| TaskControlOps | 18 |

## WhereClause Options

| Filter Type | Description | Use Case |
|-------------|-------------|----------|
| `idFilter` | Search by object IDs directly | When you know the exact ID/handle |
| `jsonPathFilter` | Search by JSONPath expressions | Filtering by field values |
| `assocFilter` | Search by associations with other objects | Finding related objects |

**Note**: Only one filter type per query. Multiple filters within `jsonPathFilter.filters` are ANDed together.

## JSONPath Filter Syntax

```json
"where": {"jsonPathFilter": {"filters": [
  {"property": "$.path.to.field", "cmp": <CompareOp>, "value": "<value>"}
]}}
```

### JSONPath Expressions

- Start with `$.` prefix
- Use `.` to navigate nested fields
- Use `.*` for wildcard in maps/lists

**Examples:**
- `$.name` - Top-level field
- `$.schedulerSpec.id.name` - Nested field
- `$.status.*.resourceAllowance` - Wildcard through map keys

## CompareOp Values

| Operator | Value | Notes |
|----------|-------|-------|
| EQ (equals) | 1 | Works with any value types |
| GT (greater than) | 2 | Numeric values only |
| GE (greater or equal) | 3 | Numeric values only |
| LT (less than) | 4 | Numeric values only |
| LE (less or equal) | 5 | Numeric values only |
| IS_TRUE | 6 | **Not supported by server.** Use `cmp:1` (EQ) with `"value":"true"` instead |
| IS_FALSE | 7 | **Not supported by server.** Use `cmp:1` (EQ) with `"value":"false"` instead |
| REGEX | 10 | String values only (RE2 with POSIX syntax) |
| PREFIX | 11 | String values only |
| NE (not equals) | 12 | Works with any value types |
| SIZE_EQ | 13 | Container size equals |
| SIZE_NE | 14 | Container size not equals |
| SIZE_GT | 15 | Container size greater than |
| SIZE_GE | 16 | Container size greater or equal |
| SIZE_LT | 17 | Container size less than |
| SIZE_LE | 18 | Container size less or equal |

## Association Filter Syntax

```json
"where": {"assocFilter": {"assocObjectType": <N>, "assocObjectIds": ["id1"]}}
```

**Only the combinations below are supported** — unsupported associations return an error.

| You Have (assocObjectType) | You Want (from) | `from` | `assocObjectType` |
|---|---|---|---|
| Job (1) | Reservation | 2 | 1 |
| Job (1) | Task | 4 | 1 |
| Job (1) | Server | 5 | 1 |
| Job (1) | TaskControlOps | 18 | 1 |
| Reservation (2) | Job | 1 | 2 |
| Reservation (2) | Allowance | 11 | 2 |
| Task (4) | Server | 5 | 4 |
| Task (4) | TaskUpdateHistoryRecord | 8 | 4 |
| Server (5) | Task | 4 | 5 |
| Server (5) | ChefShard | 15 | 5 |
| ServiceID (7) | Job | 1 | 7 |

**NOT supported (use workarounds):**
- Server by Reservation — use Job as intermediate: Reservation → Jobs → Servers
- Task by Reservation — use Job as intermediate: Reservation → Jobs → Tasks
- Reservation by Allowance — no association exists. Instead: (1) query Allowance by name (`from:11`, jsonPathFilter `$.name`) — the UUID is returned in `objectIds`, then (2) query Reservations (`from:2`) with jsonPathFilter `{"property": "$.allowanceId", "cmp": 1, "value": "<UUID>"}`

## What Does NOT Exist

These SQL features are **NOT supported**:

| Feature | Status |
|---------|--------|
| LIMIT N | ❌ |
| OFFSET N | ❌ |
| ORDER BY | ❌ |
| GROUP BY | ❌ |
| COUNT(*) | ❌ Count client-side |
| DISTINCT | ❌ Dedupe client-side |
| JOIN | ❌ Use assocFilter |
| Subqueries | ❌ |

## Response Format

```json
{
  "objectIds": ["id1", "id2"],
  "jsonObjects": ["{...}", "{...}"]
}
```

- `objectIds`: List of matching object IDs
- `jsonObjects`: List of JSON strings (parse with `json.loads()`)

## Runnable Examples

> **Copy-paste ready.** Every example below is a single-line command that works as-is. Replace the placeholder IDs with real values.

### Look up a job by handle (ID filter)
```bash
thriftdbg sendRequest search '{"request":{"select":{"allFields":{}},"from":1,"where":{"idFilter":{"ids":["tsp_prn/myteam/my_job"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

### Search jobs by name pattern (JSONPath filter with REGEX)
```bash
thriftdbg sendRequest search '{"request":{"select":{"allFields":{}},"from":1,"where":{"jsonPathFilter":{"filters":[{"property":"$.schedulerSpec.id.name","cmp":10,"value":".*api.*"}]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

### List tasks for a job (association filter)
```bash
thriftdbg sendRequest search '{"request":{"select":{"allFields":{}},"from":4,"where":{"assocFilter":{"assocObjectType":1,"assocObjectIds":["tsp_prn/myteam/my_job"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

### Look up a reservation by name
```bash
thriftdbg sendRequest search '{"request":{"select":{"allFields":{}},"from":2,"where":{"idFilter":{"ids":["my_reservation_name"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

### Find reservations for a job (association)
```bash
thriftdbg sendRequest search '{"request":{"select":{"allFields":{}},"from":2,"where":{"assocFilter":{"assocObjectType":1,"assocObjectIds":["tsp_prn/myteam/my_job"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

### Look up a server by hostname
```bash
thriftdbg sendRequest search '{"request":{"select":{"allFields":{}},"from":5,"where":{"idFilter":{"ids":["my_hostname.facebook.com"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

### Get task update history for a task
```bash
thriftdbg sendRequest search '{"request":{"select":{"allFields":{}},"from":8,"where":{"assocFilter":{"assocObjectType":4,"assocObjectIds":["tsp_prn/myteam/my_job/0"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

### Look up an allowance by ID
```bash
thriftdbg sendRequest search '{"request":{"select":{"allFields":{}},"from":11,"where":{"idFilter":{"ids":["my_allowance_id"]}},"jsonResponseFormat":{}}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 90000 | jq
```

### Discover searchable fields for any object type (help API)
```bash
thriftdbg sendRequest help '{"request":{"objectType":1,"format":2}}' --tier tupperware.universal_search.experiment.test --request_timeout_ms 30000 | jq '.descriptions'
```
Change `objectType` to the enum value for your object type (Job=1, Reservation=2, Task=4, Server=5, etc.).
