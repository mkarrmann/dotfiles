## Example 1: Get PAGELET Page Content (Markdown)

```bash
scripts/graphql_wiki_content "Meta_Account_Access_Eng_Wiki/RL_Account_Access/Development_Guide/Backend_APIs/GraphQL/Making_GraphQL_Requests"
```

**Output:** Clean markdown starting with:
```markdown
# Using Curl

First of all, choose an entry point (graph API or XController) from [here]...
```

## Example 2: Get LEXICAL Page Content (Plain Text)

```bash
scripts/graphql_wiki_content "Meta_Account_Access_Eng_Wiki/RL_Account_Access/Development_Guide/Backend_APIs/GraphQL/Entry_Points"
```

**Output:** Plain text (not JSON):
```
1. XController
Code: FRLXMultiSiteWebGraphQLExecutorController.php
URI: https://www.meta.com/api/graphql/
Authentication: Cookie (frl_sess)
```

## Example 4: Pipe to pastry

```bash
scripts/graphql_wiki_content "Your_Wiki_Path" | pastry
```
