# Common Query Patterns

## Service Health Check

```bash
# CPU utilization (average and max across tier)
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t latest -r avg --stime=30_min
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t latest -r max --stime=30_min

# Memory usage
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.mem-used" -t latest -r avg --stime=30_min

# Request rate
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.requests.count" -t rate -r sum --stime=30_min

# Error rate
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.errors.count" -t rate -r sum --stime=30_min
```

## Latency Analysis

```bash
# P50, P95, P99 latency
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.requests.latency_ms.p50" -t "avg(300)" -r avg --stime=1_h
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.requests.latency_ms.p95" -t "avg(300)" -r avg --stime=1_h
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.requests.latency_ms.p99" -t "avg(300)" -r avg --stime=1_h

# Using aggregation types (alternative for tag-aggregated metrics)
meta ods.category query -e my.entity -k "fb303.requests.latency_ms" -a p99 --hours=1
```

## Tupperware Job Monitoring

```bash
# Memory usage (total and average)
meta ods.metric query -e "twtasks(my_job)" -k "tw.mem.rss_bytes" -t latest -r sum --stime=1_h
meta ods.metric query -e "twtasks(my_job)" -k "tw.mem.rss_bytes" -t latest -r avg --stime=1_h

# CPU usage
meta ods.metric query -e "twtasks(my_job)" -k "tw.cpu.user_usec" -t rate -r sum --stime=1_h

# Task count
meta ods.metric query -e "twtasks(my_job)" -k "tw.mem.rss_bytes" -t latest -r count --stime=1_h
```

## Trend Analysis

```bash
# Last hour with 5-minute averaging
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t "avg(300)" -r avg --stime=1_h

# Last day with hourly averaging
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t "avg(3600)" -r avg --stime=1_d

# Last week
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t "avg(3600)" -r avg --stime=7_d
```

## Per-Host Breakdown

```bash
# See values for each host (no reduction)
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t latest --stime=30_min

# Top 10 by CPU usage
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t latest -r "top(10)" --stime=30_min
```

## Chart URLs and Screenshots

```bash
# Generate a shareable Canvas Fiddle URL
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" --stime=1_h --show-url

# Add visual annotation (e.g., rollout marker)
meta ods.metric query -e "smc(my.tier)" -k requests --show-url \
  --view-params '{"vmarks":[{"value":"1775399728","title":"Rollout"}]}'

# Take a screenshot for SEV artifacts or reports
meta ods.metric take-screenshot -u "https://fburl.com/canvas/xyz123"

# Screenshot with custom time range
meta ods.metric take-screenshot -u "https://fburl.com/canvas/xyz123" \
  --start-date 2026-04-01 --end-date 2026-04-07

# Upload screenshot to PixelCloud for easy sharing
meta ods.metric take-screenshot -u "https://fburl.com/canvas/xyz123" --upload
```

## Category Investigation

```bash
# Find categories owned by a team
meta ods.category list --oncall=my_team --limit=20

# Check category metadata (limits, defcon, retention)
meta ods.category metadata -c my_category

# Find categories over quota
meta ods.category list --limiter-status=blocked --limit=10

# Find critical defcon categories
meta ods.category list --defcon=crit --limit=10
```

## Common Key Patterns

| Pattern | Description |
|---------|-------------|
| `system.cpu-idle` | CPU idle percentage |
| `system.cpu-util-pct` | CPU utilization percentage |
| `system.mem-used` | Memory used |
| `system.mem-free` | Memory free |
| `system.load-1` | 1-minute load average |
| `tw.mem.rss_bytes` | Tupperware RSS memory |
| `tw.cpu.user_usec` | Tupperware CPU user time |
| `fb303.requests.count` | FB303 request count |
| `fb303.requests.latency_ms.p50` | FB303 p50 latency |
| `fb303.requests.latency_ms.p99` | FB303 p99 latency |
| `fb303.errors.count` | FB303 error count |

## Real-World Investigation Workflow

```bash
# Step 1: Discover entities in a tier
meta ods.metric resolve -e "smc(my.tier, recurse=.*)"

# Step 2: Find available keys
meta ods.metric keys -e "my.entity" --prefix=fb303 --limit=50

# Step 3: Check service health
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t latest -r avg --stime=30_min
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t latest -r max --stime=30_min

# Step 4: Check for error spikes
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.errors.count" -t rate -r sum --stime=1_h
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.requests.count" -t rate -r sum --stime=1_h

# Step 5: Compare to baseline (last day)
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t "avg(3600)" -r avg --stime=1_d

# Step 6: Check latency percentiles
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.requests.latency_ms.p50" -t "avg(300)" -r avg --stime=1_h
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "fb303.requests.latency_ms.p99" -t "avg(300)" -r avg --stime=1_h

# Step 7: Find hotspots
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" -t latest -r "top(10)" --stime=30_min

# Step 8: Get shareable chart URL
meta ods.metric query -e "smc(my.tier, recurse=.*)" -k "system.cpu-util-pct" --stime=1_h --show-url

# Step 9: Check category quota and configuration
meta ods.category metadata -c my_category
```
