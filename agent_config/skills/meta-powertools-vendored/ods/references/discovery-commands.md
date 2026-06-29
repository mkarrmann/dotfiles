# Discovery Commands

Commands for finding entities, keys, and understanding the ODS data model.

## meta ods.metric keys — Discover Keys

Lists available metric keys for an entity using the EKI (Entity Key Index) API.

```bash
# All keys for an entity
meta ods.metric keys -e "my.host.name"

# Filter by prefix
meta ods.metric keys -e "my.host.name" --prefix=system

# Filter by regex
meta ods.metric keys -e "my.host" --regex="cpu\\..*"

# Limit results
meta ods.metric keys -e "my.host.name" --limit=20

# JSON output
meta ods.metric keys -e "my.host.name" --output=json
```

| Flag | Description | Default |
|------|-------------|---------|
| `-e`, `--entity` | Entity name (required) | — |
| `-p`, `--prefix` | Filter keys by prefix | — |
| `--regex` | Filter keys by regex pattern | — |
| `-l`, `--limit` | Maximum number of keys | 100 |

## meta ods.metric resolve — Resolve Entity Patterns

Resolves entity selector expressions to concrete entity names.

```bash
# Resolve SMC tier to host names
meta ods.metric resolve -e "smc(my.service.tier)"

# Resolve regex pattern
meta ods.metric resolve -e "regex(web\\.prod\\..*)" --limit=20

# Resolve Tupperware job
meta ods.metric resolve -e "tw(my.tupperware.job)"

# Resolve with twtask selector
meta ods.metric resolve -e "smc(my.tier, selector=twtask)"

# Discover child tier names
meta ods.metric resolve -e "smc(my.tier, recurse=.*, selector=tier)"

# JSON output
meta ods.metric resolve -e "smc(my.tier)" --output=json
```

| Flag | Description | Default |
|------|-------------|---------|
| `-e`, `--entity` | Entity selector expression (required) | — |
| `-l`, `--limit` | Maximum number of entities | 50 |

**Supported selectors**: `smc(tier)`, `regex(pattern)`, `tw(job)`, `twtasks(job)`, `cluster(...)`, `tag(...)`, `map(...)`

## meta ods.metric suggest-entities — Fuzzy Entity Search

Autocomplete-style entity name discovery. Searches both EKI and Rapido hint APIs concurrently.

```bash
# Search for entities matching a prefix
meta ods.metric suggest-entities -q "web.prod"

# Broad search
meta ods.metric suggest-entities -q "cache" --limit=10

# JSON output
meta ods.metric suggest-entities -q "ods" --output=json
```

| Flag | Description | Default |
|------|-------------|---------|
| `-q`, `--query` | Search query (required) | — |
| `-l`, `--limit` | Maximum results | 50 |

**When to use**: When you don't know the exact entity name. Unlike `resolve` (which requires a valid selector), `suggest-entities` does fuzzy matching on partial names.

## meta ods.metric suggest-keys — Fuzzy Key Search

Key/metric name autocomplete using the Rapido key suggestion API.

```bash
# Search for keys matching a term
meta ods.metric suggest-keys -q "cpu"

# Scoped to a specific entity
meta ods.metric suggest-keys -q "mem" -e "my.host.name"

# Scoped to multiple entities
meta ods.metric suggest-keys -q "disk" -e "host1,host2" --limit=20

# JSON output
meta ods.metric suggest-keys -q "network" --output=json
```

| Flag | Description | Default |
|------|-------------|---------|
| `-q`, `--query` | Search query (required) | — |
| `-e`, `--entity` | Scope to entity (comma-separated for multiple) | — |
| `-l`, `--limit` | Maximum results | 50 |

**When to use**: When you don't know the exact key name. Use `--entity` to scope results to a specific entity for more relevant suggestions.

## meta ods.metric related — Find Related Metrics

Discovers relationships between entities and keys using two modes.

```bash
# Find keys common across multiple entities
meta ods.metric related --entities="host1,host2"

# Find entities that share specific keys
meta ods.metric related --keys="system.cpu-util-pct,system.mem-used"

# Filter results by metric category
meta ods.metric related --entities="host1,host2" --category="system"

# JSON output
meta ods.metric related --keys="fb303.thrift.requests" --limit=20 --output=json
```

| Flag | Description |
|------|-------------|
| `--entities` | Comma-separated entity names — find common keys |
| `--keys` | Comma-separated key names — find entities with all these keys |
| `--category` | Filter results by metric category prefix (e.g., "system", "tw", "fb303") |
| `-l`, `--limit` | Maximum results (default: 50) |

At least one of `--entities` or `--keys` must be provided.

**Use cases**:
- Cross-service debugging: find which metrics two hosts share
- Discovering entities that report a specific metric
- Finding related metrics within a category

## meta ods.category schema — Browse Entity/Key Schema

Browse the ODS entity/key schema via the EKI service.

```bash
# List keys for an entity
meta ods.category schema -e "my.entity"

# Filter keys by prefix
meta ods.category schema -e "my.entity" --filter=cpu

# Search for entities by prefix
meta ods.category schema -e "my" --entities-only

# Limit results
meta ods.category schema -e "my.entity" --limit=50

# JSON output
meta ods.category schema -e "my.entity" --output=json
```

| Flag | Description | Default |
|------|-------------|---------|
| `-e`, `--entity` | Entity name (exact match for keys, prefix for `--entities-only`) | required |
| `-f`, `--filter` | Filter keys by prefix | — |
| `--entities-only` | Search for entities instead of keys | — |
| `--limit` | Maximum results | 100 |

**When to use `schema` vs `keys`**:
- `meta ods.metric keys` — lightweight key discovery with prefix/regex
- `meta ods.category schema` — fuller EKI browsing, also supports entity search via `--entities-only`

## Discovery Workflow

Typical entity/key discovery flow:

```bash
# Step 1: Find the entity
# If you know the name roughly:
meta ods.metric suggest-entities -q "my.service"

# If you know it's an SMC tier:
meta ods.metric resolve -e "smc(my.service.tier)"

# Step 2: Find available keys for that entity
meta ods.metric keys -e "my.entity" --prefix=fb303

# Or fuzzy search:
meta ods.metric suggest-keys -q "latency" -e "my.entity"

# Step 3: Query the metric
meta ods.metric query -e "my.entity" -k "fb303.requests.latency_ms.p99" --stime=1_h

# Step 4: Expand to the full tier
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.requests.latency_ms.p99" -t "avg(300)" -r avg --stime=1_h
```
