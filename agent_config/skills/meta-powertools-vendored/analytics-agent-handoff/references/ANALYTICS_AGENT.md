# Analytics Agent Capabilities Reference

Analytics Agent is Meta's AI-powered assistant for data science and engineering tasks. It provides intelligent assistance for SQL query development, data analysis, experiment analysis, and data visualization.

## Core Capabilities

### SQL Query Development
- Write, validate, and execute Presto SQL queries against Meta's data warehouse
- SQL validation and linting before execution
- Query timeout management (60s default, up to 270s)
- Role-based access control and ACL enforcement

### Data Discovery
- Search for Hive tables using keywords or semantic search
- Get detailed table information (schema, columns, metadata)
- Search data namespaces for relevant tables
- Look up term definitions and explanations

### Experiment Analysis (Deltoid)
- Analyze A/B tests and experiments
- Support for QE, GK, and QRT experiments
- Multiple view types: Delta, Time Series, Days Since First Exposure
- Metric and population filtering
- Find existing Deltoid queries by creator, experiment, or metrics

### Data Visualization
- Create metric boxes, line charts, pie charts, and bar charts
- Generate visualizations from Presto query results
- Extract dimensions from query results for visualization

### Scuba Analytics
- Find and query Scuba datasets for time-series analysis
- Get dataset information and column details
- Execute Scuba queries with screenshot capture

### Python Execution
- Execute Python code for data analysis and calculations
- Available libraries: pandas, numpy, matplotlib, seaborn, scipy

## Available Tools

| Tool | Purpose |
|------|---------|
| `run_presto_query` | Execute Presto SQL queries |
| `lint_sql_query` | Validate SQL syntax and structure |
| `data_hive_table_info` | Get Hive table schema and metadata |
| `namespace_search` | Search for tables in data namespaces |
| `idata_search` | Search for datasets using keywords |
| `wut` | Look up term definitions |
| `deltoid_run_query` | Run experiment analysis queries |
| `deltoid_find_query` | Find existing Deltoid queries |
| `deltoid_analyze` | Detailed analysis of experiment results |
| `knowledge_search` | Search internal documentation |
| `personal_sql_history` | Access user's previous SQL queries |
| `scuba_find_dataset` | Find Scuba datasets |
| `scuba_dataset_info` | Get Scuba dataset details |
| `run_python` | Execute Python code |

## Data Sources

Analytics Agent can query:
- **Hive tables** - Meta's data warehouse (production data)
- **Scuba** - Real-time time-series analytics
- **Deltoid** - Experiment analysis platform
- **M360** - Metric definitions and dimensions
- **Unidash** - Dashboard widgets and metrics
- **iData** - Data catalog and discovery

## Privacy and Security

**Note:** When used through Claude Code, Analytics Agent currently runs in **Lite mode** (no DSS4 data) because Claude Code is not yet DSS4-approved. All results are aggregated with built-in privacy safeguards.

**Current (Analytics Agent Lite via Claude Code):**
- Aggregated results only — no PII or user-level data
- Built-in privacy safeguards

**After Claude Code DSS4 approval (automatic upgrade):**
- DSS4 data access
- ACL enforcement — uses user's own data access permissions
- DAPR coverage — privacy-compliant analytics usage

## Limitations

- Cannot execute queries on data the user doesn't have access to
- Query timeout is 270 seconds maximum
- Response size limits apply to large result sets
- Some tools are feature-gated and may not be available to all users

## External Links

- [Analytics Agent](https://www.internalfb.com/analytics-agent) - Main interface
- [Analytics Agent Wiki](https://www.internalfb.com/wiki/AI_for_Data_Analytics/Analytics_Agent/) - Documentation
- [Cookbooks & Best Practices](https://www.internalfb.com/wiki/AI_for_Data_Analytics/Analytics_Agent/Cookbooks/) - Usage guides
- [Analytics Agent FYI](https://fb.workplace.com/groups/1402827481052157) - Workplace group
- [Analytics Agent Feedback](https://fb.workplace.com/groups/586845090590508) - Feedback group
