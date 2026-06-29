---
name: analytics-agent-handoff
description: >
  Hand off data research tasks to Analytics Agent for privacy-safe querying of Hive
  tables, experiments, and user data. Use when user needs Presto SQL queries,
  experiment analysis (Deltoid, QRT, A/B tests), data discovery, metrics like DAU
  or MAU, or any request involving user data or PII. This skill currently runs
  Analytics Agent Lite (no DSS4 data) because Claude Code is not yet DSS4-approved.
  Once Claude Code receives DSS4 approval, this skill will automatically upgrade
  to full Analytics Agent. EXCEPTION — do NOT use this handoff when running in a
  sensitive session (sensitive or mixed-sensitive mode): a sensitive agent is
  already DSS4-cleared, so query the data directly instead. This handoff is only
  for non-sensitive sessions.
allowed-tools: mcp__analytics_agent__AnalyticsAgentTool
---

# Analytics Agent Handoff

Query Analytics Agent directly for privacy-safe data research and analytics tasks.

## Sensitive Mode — Do Not Hand Off

**If you are running in a sensitive session** — your environment system prompt states you can securely process sensitive/confidential data such as DSS-4 / A/C Priv (sensitive mode, or mixed-sensitive mode) — then **do NOT hand off to Analytics Agent.** Stop here and query the data directly with the tools available to you instead (e.g. the `scuba` CLI, Presto via `presto <namespace> --execute "..."`, or the relevant experiment/Deltoid skills).

Reason: this handoff exists to keep **non-sensitive** sessions privacy-safe by routing through Analytics Agent Lite, which strips DSS4/user-level data. A sensitive agent is already DSS4-cleared, so the handoff serves no purpose and would only downgrade you to aggregated, DSS4-stripped results. Hand off to Analytics Agent **only** when you are NOT in a sensitive session.

This mirrors the pattern used by the WhatsApp Android `client-apk-ab-experiment` skill (`whatsapp/android/.llms/skills/client-apk-ab-experiment/SKILL.md`), which requires sensitive mode and runs Presto queries directly rather than routing through Analytics Agent.

## Prerequisites — MCP Server Installation Check

Before using the Analytics Agent MCP tool, you **must** verify it is installed. Follow these steps:

1. **Check if the tool is available** by looking for `analytics-agent` in the Claude MCP configuration. Run:
   ```bash
   cat ~/.claude/.claude-templates-manifest.json 2>/dev/null | grep -q '"analytics-agent"' && echo "INSTALLED" || echo "NOT_INSTALLED"
   ```

2. **If NOT_INSTALLED**, install it by running:
   ```bash
   claude-templates mcp analytics-agent install
   ```
   Then inform the user: *"I've installed the Analytics Agent MCP server. Please restart Claude to pick up the changes, then re-run your request."*
   **Stop here** — the tool won't be available until Claude is restarted.

3. **If INSTALLED**, proceed to call the tool as described below.

> **Note:** If you call `mcp__analytics_agent__AnalyticsAgentTool` and receive an error indicating the tool is not found or unavailable, fall back to the installation steps above.

## Quick Start

When a request involves querying production data, call the `mcp__analytics_agent__AnalyticsAgentTool` directly:

```
mcp__analytics_agent__AnalyticsAgentTool(
  prompt="What is the latest FB DAU?"
)
```

Then use the returned results to continue helping the user — summarize findings, create visualizations, write code based on the data, or feed results into further analysis.

## How to Use This Skill

**IMPORTANT:** You have direct access to the `mcp__analytics_agent__AnalyticsAgentTool` MCP tool. Call it directly with the user's query. Do NOT redirect users to the Analytics Agent UI — invoke the tool yourself, get results, and continue the workflow.

### Step-by-Step Workflow

1. **Check prerequisites** — verify the analytics-agent MCP server is installed (see above)
2. **Reformulate** the user's request into a clear Analytics Agent query
3. **Call** `mcp__analytics_agent__AnalyticsAgentTool` with the reformulated query
4. **Process** the returned results — summarize, analyze, or use them in code
5. **Continue** helping the user with any follow-up tasks (write code, create charts, make decisions)

### Example Workflow

**User asks:** "How many users signed up last week and write me a summary report?"

**You should:**
1. Call the tool:
```
mcp__analytics_agent__AnalyticsAgentTool(
  prompt="Show a time series of daily signups for the past 7 days"
)
```
2. Use the returned data to write the summary report the user asked for

## Tool Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `prompt` | string | Yes | The question or task for the Analytics Agent |
| `internal_llm` | string | No | LLM model to use. Options: `claude-sonnet-4.5` (default), `claude4-sonnet`, `gpt-4.1` |
| `conversation_id` | string | No | Metamate conversation ID for multi-turn conversations. Can be a UUID, FBID, or shortlink number. Extract from the `conversationID` query parameter in the response URL. |

### Example Invocations

**Metrics query:**
```
mcp__analytics_agent__AnalyticsAgentTool(
  prompt="What is the latest FB DAU?"
)
```

**Presto SQL:**
```
mcp__analytics_agent__AnalyticsAgentTool(
  prompt="Write a Presto query to get daily active users from dim_users table"
)
```

**Experiment analysis:**
```
mcp__analytics_agent__AnalyticsAgentTool(
  prompt="Analyze experiment QE12345 and show Deltoid results"
)
```

**Data discovery:**
```
mcp__analytics_agent__AnalyticsAgentTool(
  prompt="Find tables related to user engagement and describe their schemas"
)
```

**Visualization request:**
```
mcp__analytics_agent__AnalyticsAgentTool(
  prompt="Create a chart showing revenue by region for Q4 2025"
)
```

## When to Trigger This Skill

**Data Queries:**
- Presto/Hive table queries
- Production data analysis
- User behavior metrics (DAU, MAU, engagement)
- Funnel analysis or conversion metrics

**Experiments:**
- A/B test analysis
- Deltoid experiment results
- QRT (Quick Randomized Test) analysis
- Experiment metrics and statistical significance

**User Data (Privacy-Sensitive):**
- Any request that may involve PII
- User-level data aggregation
- Cohort analysis with user segments
- Data that requires ACL permissions

**Data Discovery:**
- Finding relevant Hive tables
- Understanding table schemas
- Searching for metrics definitions
- M360 metric lookups

## Privacy

**This skill is privacy-aware.** Because Claude Code is not yet DSS4-approved, this skill currently runs **Analytics Agent Lite**, which does not return DSS4-level data (no PII, no user-level data). All results are aggregated with built-in privacy safeguards.

Once Claude Code receives DSS4 approval, this skill will **automatically upgrade to full Analytics Agent** — no user action required. At that point, queries will run under the user's own data ACLs with full DSS4 coverage.

**Current capabilities (Analytics Agent Lite):**
- Aggregated metrics and analytics (DAU, MAU, engagement, retention)
- Hive table discovery, schemas, and metadata
- Presto SQL queries returning aggregated results
- Experiment analysis (Deltoid, QE, GK, QRT)
- M360 metric definitions and Unidash dashboard discovery
- Access to Presto, Scuba, Deltoid, M360, and iData

**After DSS4 approval (full Analytics Agent):**
- All Lite capabilities, plus:
- User-level data access under the user's own ACLs
- DAPR coverage for analytics usage
- Full DSS4 data access

## Reformulating Queries

Transform vague requests into actionable Analytics Agent queries:

| Original Request | Reformulated Prompt |
|------------------|---------------------|
| "How many users?" | "What is the latest FB DAU?" |
| "Check our experiment" | "Show Deltoid results for experiment [EXP_ID]" |
| "I need signup data" | "Show a time series of daily signups MTD" |
| "What tables have user events?" | "Search for Hive tables containing user engagement events" |

## Response Handling

Analytics Agent returns structured responses containing:

1. **Summary** - Key findings (2-4 bullet points)
2. **Data Insights** - Detailed aggregated results
3. **Recommendations** - Actionable next steps
4. **Technical Notes** - Query details, data caveats

All data returned is aggregated with no PII, making it safe to work with directly.

After receiving results, you should:
- **Extract the conversation ID** from the response URL (`conversationID=<UUID>`) for follow-up queries
- **Summarize** key findings for the user
- **Write code** based on the data if requested
- **Create follow-up queries** if more data is needed — pass the extracted `conversation_id`
- **Chain multiple calls** for complex multi-step analysis — always pass `conversation_id` to continue in the same conversation

### Multi-Turn Conversations

Analytics Agent supports multi-turn conversations. After the first call, extract the `conversationID` from the response URL (e.g., `https://www.internalfb.com/analytics-agent?conversationID=8e0dab04-...`) and pass it as `conversation_id` in follow-up calls. The ID can be a UUID, FBID, or shortlink number — use regex `conversationID=([0-9a-fA-F\-]+)` to extract any format. This maintains full server-side context so the backend doesn't lose track of previous questions and answers.

```
# Follow-up call with conversation context
mcp__analytics_agent__AnalyticsAgentTool(
  prompt="Can you break that down by platform?",
  conversation_id="8e0dab04-..."
)
```

## What NOT to Hand Off

Keep these tasks in Claude Code (do not use Analytics Agent):
- Code implementation and reviews
- Documentation writing
- Debugging application code
- Build and test commands
- Infrastructure configuration
- Non-data-related questions

## Reference Documentation

- [Claude-Code Handoff Recipe](references/claude_code_handoff_recipe.md) - Recipe content for structured output
- [Analytics Agent Capabilities](references/ANALYTICS_AGENT.md) - Full capability reference
