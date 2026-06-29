# Config ACLs (Access Control)

Configerator uses ACLs to control who can modify configs.

## ACL File Types

| File | Purpose | Location |
|------|---------|----------|
| `REVIEWERS_ACL` | Controls which **users** can review/approve config changes | `source/` directory |
| `AUTOMATION_ACL` | Controls which **services/automation** can commit without review | `source/` directory |

**Important:** ACL files **always** go in the `source/` directory, never in `materialized_configs/` or `raw_configs/`. They apply recursively to all subdirectories that don't have their own ACL file.

## How ACLs Work

1. **AUTOMATION_ACL** is checked first - allows specific service identities to commit without human review
2. **REVIEWERS_ACL** is checked second - requires human review from specified reviewers
3. If no ACL exists in a directory, it inherits from the nearest parent directory

## Creating ACL Files

ACL files contain a single line: the name of a Hipster ACL that defines the actual permissions.

```bash
# Example REVIEWERS_ACL content:
configerator.my_team.reviewers

# Example AUTOMATION_ACL content:
configerator.my_team.automation
```

The Hipster ACL (managed separately) contains the actual list of allowed identities.

## Checking ACL Status

```bash
# Check ACL reviewers for a mutation
conf acl check <mutation_id>

# Query ACL check logs in Scuba
# Table: configo_acl_checks
```

## Common ACL Scenarios

### Protecting Sensitive Configs

```
source/my_team/sensitive_configs/
├── REVIEWERS_ACL          # Points to Hipster ACL with your team
├── AUTOMATION_ACL         # Points to Hipster ACL with allowed services
└── config.cconf
```

### Blocking All Automation

Create an AUTOMATION_ACL pointing to `configerator.no_permissions` to prevent any automation from modifying configs.

### Protecting raw_configs

To protect `raw_configs/foo/bar/`, put the ACL in `source/foo/bar/`.

## Automation Identity Priority

When granting automation access, use the most specific identity type:

1. DATA_PROJECT (most specific)
2. INTERN_CONTROLLER
3. ASYNC_JOB_ID
4. SANDCASTLE_TAG
5. SANDCASTLE_CMD
6. SERVICE_IDENTITY (least specific)

## What Counts as Automation?

Config edits not made by humans are considered automation:
- Chronos jobs
- Tupperware services
- Smart Platform
- Conveyor
- Sandcastle

**Note:** Tools running on devserver/on-demand are NOT qualified automation services.

## ACL Troubleshooting

### "Missing ACL Check" Lint Warning

This means you added one ACL type but not the other. The missing type will inherit from a parent directory. If that's acceptable, ignore the warning. Otherwise, create the missing ACL.

### "Unconstrained automation ACL" Error

A parent directory has an AUTOMATION_ACL that allows everyone, making your REVIEWERS_ACL useless. Solution: Either constrain the parent's AUTOMATION_ACL or create a constrained AUTOMATION_ACL in your directory.

### Finding Automation Identities

Use the `configo_acl_checks` Scuba table to find which automation identities are committing to your directory, then add them to your Hipster ACL.

## ACL Best Practices

1. **Use REVIEWERS_ACL** for sensitive configs that need human oversight
2. **Constrain AUTOMATION_ACL** to only the specific services that need access
3. **Query Scuba** before constraining to avoid breaking existing automation
4. **Add REVIEWERS_ACL** to protect your AUTOMATION_ACL from unauthorized changes
5. **Use the most specific identity type** when granting automation access
