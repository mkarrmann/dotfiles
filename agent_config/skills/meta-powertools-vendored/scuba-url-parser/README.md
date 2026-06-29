# Scuba URL Parser Skill

A custom Claude Code skill that parses Scuba query URLs and executes them using
scuba-cli Claude skill.

## Overview

This skill takes a Scuba query URL (from the internal Scuba web interface) and:

1. Parses the URL to extract query parameters
2. Decodes the drillstate JSON configuration
3. Generates valid Scuba SQL
4. Executes the query using scuba-cli Claude skill

**NOTE:** For shortened fburl.com URLs, use the `fburl-cli` skill to expand them first before using this skill.

## Usage

### Using the Skill in Claude Code

Once installed, you can invoke the skill by providing a Scuba query URL:

```
Use the scuba-url-parser skill with this URL: https://www.internalfb.com/intern/scuba/query/?dataset=mgp_data_service_app_logs&drillstate=...
```

If a scuba url (www.internalfb.com/intern/scuba/...) is provided, ask to parse
using this skill.

### Using the Python Script Directly

You can also use the helper Python script directly:

```bash
# From your fbcode directory:
buck2 run //claude-templates/components/skills/scuba-url-parser/scripts:parse_scuba_url -- 'https://www.internalfb.com/intern/scuba/query/?dataset=mgp_data_service_app_logs&drillstate=...'
```

**Note:** This script only accepts full Scuba query URLs. If you have a shortened URL (fburl.com), it must be expanded before passing it to this script. URL expansion is handled separately and is not the responsibility of this script.

## Example

Given a Scuba URL like:

```
https://www.internalfb.com/intern/scuba/query/?dataset=mgp_data_service_app_logs&drillstate=%7B%22purposes%22%3A[]%2C%22end%22%3A%22now%22%2C%22start%22%3A%22-10080%20minutes%22...&pool=uber&view=table_client
```

The skill will:

1. **Parse the URL components:**
   - Dataset: `mgp_data_service_app_logs`
   - Pool: `uber`
   - Time range: `-10080 minutes` to `now`
   - Dimensions: `["data_solution"]`
   - Derived columns: `coverage`, `updated_requests`

2. **Generate SQL:**

   ```sql
   SELECT
       data_solution,
       100.0*CAST_AS_DOUBLE(SUM(IF(product_vertical is not null, 1, 0)))/CAST_AS_DOUBLE(COUNT(*)) AS coverage,
       100.0*CAST_AS_DOUBLE(SUM(IF(product_vertical is not null, 1, 0))) AS updated_requests
   FROM mgp_data_service_app_logs
   WHERE
       time > now() - 10080 * 60
       AND time < now()
   GROUP BY data_solution
   ORDER BY coverage DESC
   LIMIT 200
   ```

3. **Execute with scuba-cli:**
   ```bash
   scuba-cli query --dataset mgp_data_service_app_logs --pool uber --sql "..."
   ```

## Components

### SKILL.md

The main skill definition file that contains instructions for parsing Scuba URLs
and generating SQL.

### parse_scuba_url.py

A Python helper script that:

- Parses Scuba query URLs
- Extracts drillstate JSON
- Converts query components to SQL
- Generates scuba-cli commands

### Key Features

- **Time range conversion**: Converts expressions like "-10080 minutes" to
  proper SQL time functions
- **Derived column support**: Handles complex SQL expressions from derived
  columns
- **Dimension grouping**: Properly groups results by specified dimensions
- **Sorting and limiting**: Applies ORDER BY and LIMIT clauses
- **Filter support**: Extracts and applies constraints from the drillstate

## Requirements

- Python 3.9+
- scuba-cli (for executing queries)
- Access to internal Scuba datasets

## Limitations

- Complex filter expressions may require manual adjustment
- Some advanced Scuba features may not be fully supported
- Always review the generated SQL before execution

## Troubleshooting

### URL parsing errors

- Ensure the URL is properly quoted when passing as an argument
- Check that the drillstate parameter is URL-encoded

### SQL generation issues

- The generated SQL is shown before execution - review it for accuracy
- Some complex derived columns may need manual adjustment
- Time range expressions should be in Scuba's format ("-N minutes/hours/days")

### scuba-cli errors

- Verify you have access to the dataset
- Check that the pool name is correct
- Ensure your scuba-cli installation is up to date

## Contributing

To improve this skill:

1. Test with various Scuba URLs
2. Report issues with specific URL patterns
3. Submit enhancements for better SQL generation

## License

Internal use only - Meta Platforms, Inc.
