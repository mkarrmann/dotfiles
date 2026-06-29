---
name: wiki-query
description: Query internal wiki pages and retrieve their markdown/content via GraphQL. This is MUCH PREFERRED over using curl to fetch wiki HTML pages.
---

# Wiki Query Skill

Get clean markdown content from internal wiki pages using GraphQL instead of curl.

## Extract Wiki Page Path from URL

**URL:** `https://www.internalfb.com/wiki/Main_Page/Sub_Page/Article`
**Path:** `Main_Page/Sub_Page/Article`

Remove the `https://www.internalfb.com/wiki/` prefix and any trailing slash.

## Get Wiki Content

NOTE: This script path is relative to this skill definition.

```bash
scripts/graphql_wiki_content "Your_Wiki_Path"
```

This works for both PAGELET (markdown) and LEXICAL (newer editor) wiki pages.

## Example

```bash
scripts/graphql_wiki_content "Meta_Account_Access_Eng_Wiki/RL_Account_Access/Development_Guide/Backend_APIs/GraphQL/Making_GraphQL_Requests"
```

**Output:** Clean markdown starting with `# Using Curl...`

## Why Use GraphQL Instead of curl?

- **Clean content**: Get markdown/plain text directly, not HTML with navigation
- **Structured**: JSON response with clear fields
- **Reliable**: Official API, not screen-scraping
- **Efficient**: Only fetch what you need

## List Wiki Subpages

To list all subpages of a wiki page, use the `WikiUtil` Hack script. This must be run from the `www` repository.

```bash
phps WikiUtil -l "<wiki_path>"
```

### Example

```bash
phps WikiUtil -l "Workrooms/Development_environment_(WWW)/Technical_concepts_&_deep_dives"
```

**Output:**
```
Listing subpages of Workrooms/Development_environment_(WWW)/Technical_concepts_&_deep_dives ...
- Workrooms/Development_environment_(WWW)/Technical_concepts_&_deep_dives - editor type: Easy Edit Mode 2.0
- Workrooms/Development_environment_(WWW)/Technical_concepts_&_deep_dives/Meetings - editor type: Redirect
- Workrooms/Development_environment_(WWW)/Technical_concepts_&_deep_dives/WWW_Development - editor type: Easy Edit Mode 2.0
...
Done.
```

### Other WikiUtil Commands

```bash
phps WikiUtil -h                          # Show all options
phps WikiUtil -a <unixname>               # List pages created by a user
phps WikiUtil --list-empty-pages <path>   # List empty subpages
phps WikiUtil --list-deleted-wikis <path> # List deleted subpages
phps WikiUtil -wcs <path>                 # Count words in subpages
```

**Note:** `phps` runs Hack ScriptController classes and requires the www repository environment.
