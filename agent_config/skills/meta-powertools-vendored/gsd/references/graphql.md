# GSD GraphQL Operations Reference

GraphQL operations for cases the `gsd` and `meta tasks.*` CLIs don't cover, or where the GraphQL path has different behavior (e.g. no 2FA prompt). Discovered through empirical testing — file gaps in this list as you find them.

## When to Use GraphQL

Prefer the CLIs (`gsd`, `meta tasks.*`) for everything they support. Reach for GraphQL only when:

- The operation isn't exposed via CLI (e.g. **custom field definition creation**)
- The CLI requires 2FA you can't provide in an automated context (e.g. **milestone deletion**)
- You need a one-off shape the CLI doesn't return (e.g. looking up a task FBID by number for use in another mutation)
- You're discovering schema for a new operation you plan to wrap

## Running GraphQL Queries

Two ways to run the queries below:

```bash
# 1. From the command line via jf
jf graphql --query 'query { task(number: 12345) { id task_title } }'
jf graphql --query 'mutation { ... }'

# 2. Interactive web UI (better for iterating on a query)
# https://www.internalfb.com/graphiql/intern
```

> **Note:** All GSD-related types use the `tasks_gsd_*` prefix (e.g. `tasks_gsd_milestone`, `tasks_gsd_deliverable_custom_dropdown_field`).

## Task FBID Lookup

GSD task numbers (`T12345`) are the human-facing identifier. Most GraphQL mutations want the underlying FBID. Look it up with:

```graphql
query {
  task(number: 12345) {
    id
    task_title
  }
}
```

```bash
# Via jf
jf graphql --query 'query { task(number: 12345) { id task_title } }'
```

The returned `id` is the FBID (e.g. `"1567364077929152"`) suitable for use in mutation inputs.

## Custom Field Operations

The CLI can list and update custom field **values** (`meta tasks.gsd.custom-field update --task-number=T12345 --field-name="Target Milestone" --value="M0"`), but creating new field **definitions** is GraphQL-only.

### Create Custom Dropdown Field

```graphql
mutation {
  xfb_create_tasks_gsd_deliverable_custom_dropdown_field(data: {
    domain: "<TEAM_ID>"
    label: "Field Name"
    options_with_color: [
      {value: "Option1", color: BLUE}
      {value: "Option2", color: GREEN}
    ]
  }) {
    tasks_gsd_deliverable_custom_dropdown_field {
      id
      label
    }
  }
}
```

**Available colors:** `BLUE`, `CYAN`, `GRAY`, `GREEN`, `ORANGE`, `PINK`, `PURPLE`, `RED`, `TEAL`, `WHITE`, `YELLOW`.

### Create Other Field Types

Use the matching mutation for the field type you need:

| Field Type | Mutation |
|------------|----------|
| Numeric | `xfb_create_tasks_gsd_deliverable_custom_numeric_field` |
| Text | `xfb_create_tasks_gsd_deliverable_custom_text_field` |
| People | `xfb_create_tasks_gsd_deliverable_custom_people_field` |

Input shape for non-dropdown fields drops `options_with_color` and uses `domain` + `label` only. Use `graphmate.subschema` (below) to confirm the exact input type for the field kind you need.

## Milestone Operations

### Delete Milestone (no 2FA)

`gsd milestone delete <ID>` and `meta tasks.gsd.milestone delete` both prompt for 2FA, which can't be satisfied from automated contexts. The GraphQL path skips that prompt:

```graphql
mutation {
  delete_tasks_gsd_milestone(data: {
    tasks_gsd_milestone_id: "<MILESTONE_ID>"
  }) {
    deleted_id
  }
}
```

> **Tip:** Use this when scripting milestone cleanup. Otherwise prefer `gsd milestone delete` so the 2FA gate stays in the loop for interactive use.

### Add Milestone Blocker

The `gsd milestone add-blockers` CLI is the preferred path (handles tasks and milestones via prefix). The GraphQL alternative below is useful when you already have FBIDs and want a single mutation:

```graphql
mutation {
  xfb_upsert_blocker_tasks_object_with_timeline_blockers(data: {
    tasks_object_with_timeline_blockers_id: "<BLOCKED_FBID>"
    blocker: "<BLOCKER_FBID>"
  }) {
    tasks_object_with_timeline_blockers { id }
    timeline_blocker { id }
  }
}
```

Both `tasks_object_with_timeline_blockers_id` and `blocker` are FBIDs. For milestones, this is the milestone FBID; for tasks, look it up via the Task FBID Lookup query above.

### Remove Milestone Blocker

Same input shape, swap the mutation name:

```graphql
mutation {
  xfb_remove_blocker_tasks_object_with_timeline_blockers(data: {
    tasks_object_with_timeline_blockers_id: "<BLOCKED_FBID>"
    blocker: "<BLOCKER_FBID>"
  }) {
    tasks_object_with_timeline_blockers { id }
  }
}
```

## Schema Discovery

When you need a mutation or type that isn't documented here, ask the schema directly via `graphmate`.

### Search by Description

Find candidate fields/mutations by keyword:

```graphql
query {
  graphmate {
    search(queries: ["GSD milestone"]) {
      sdl
    }
  }
}
```

For mutations specifically, scope the search:

```graphql
query {
  graphmate {
    search(queries: ["GSD milestone"], parent_type: "Mutation") {
      sdl
    }
  }
}
```

### Get Detailed Type Info

Once you know the type or field name, pull its full SDL definition:

```graphql
query {
  graphmate {
    subschema(field_or_type: "TasksGSDMilestone", depth: 2) {
      sdl
    }
  }
}
```

`depth: 2` expands one level of nested types — bump it up for richer context, down for terser output.

## Known Limitations

- **Custom field deletion via API is broken.** Server returns an exception. Use the GSD web UI (`https://www.internalfb.com/gsd/`) for this.
- **Section delete still requires 2FA.** No GraphQL equivalent of `delete_tasks_gsd_milestone` was found for sections — `meta tasks.gsd.section delete` (with its 2FA prompt) is the only path.
- **`meta tasks.gsd.custom-field` is values-only.** It can list and update existing field values but cannot create field definitions — use the GraphQL mutations above.

## Additional Resources

- **GraphiQL (interactive)**: https://www.internalfb.com/graphiql/intern
- **GSD Wiki**: https://www.internalfb.com/wiki/GSD/
- **GSD support**: https://fb.workplace.com/groups/tasks.fyi
