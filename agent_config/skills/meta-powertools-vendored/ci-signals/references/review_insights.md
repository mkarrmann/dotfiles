# query_review_insights - Review Insights (Confucius Comments, AI Review)

These include:
- **Confucius Comment** / **Automated Code Reviewer** / **Devmate Reviewer** - AI-generated code review commentary
- **AI Test Quality Check** - AI analysis of test quality

```bash
# Get all Review Insights
scripts/query_review_insights D92697619

# Get only WARNING signals (like Confucius Comments)
scripts/query_review_insights D92697619 --status WARNING

# Get raw JSON output
scripts/query_review_insights D92697619 --raw
```

Returns formatted JSON with:
- Signal name and status
- Full message content (review summary, recommendation, risk level, etc.)
- External URIs if available

**Example output:**
```json
{
  "name": "Confucius Comment",
  "status": "WARNING",
  "functional_type": "REVIEW_INSIGHTS",
  "message": "**Summary:** Removes unused variable assignments...\n\n**Recommendation:** Accept - The refactoring is safe...\n\n**Risk Level:** Low - No functional changes...",
  "external_uris": []
}
```
