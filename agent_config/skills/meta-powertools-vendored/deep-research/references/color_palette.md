# Trust Gate Color Palette (Colorblind-Accessible)

> **Use colorblind-accessible colors. PASS=blue, NEVER green.** This is a shared accessibility standard.

## Tag → Color → Hex

| Tag | Semantic | RGB | Hex | Use cases |
|---|---|---|---|---|
| `[VERIFIED]` | PASS | (0.0, 0.4, 0.8) | `#0066cc` | finding verified against current source |
| `[LANDED]` | PASS (diff) | (0.0, 0.4, 0.8) | `#0066cc` | diff status `Closed` |
| `[STALE]` | PARTIAL | (0.85, 0.65, 0.0) | `#d9a600` | content drifted but symbol present; OR diff non-Landed when needs_diff_landed=true |
| `[DRAFT]` | PARTIAL (diff) | (0.85, 0.65, 0.0) | `#d9a600` | diff status `Draft / NeedsReview / Accepted` |
| `[UNVERIFIED]` | FAIL | (0.85, 0.4, 0.0) | `#d96600` | could not verify (no evidence, or no match) |
| `[HALLUCINATED]` | FAIL+critical | (0.85, 0.0, 0.0) | `#d90000` | file/diff does not exist (Symbol Safety violation) |
| `[ABANDONED]` | gray (diff) | (0.55, 0.55, 0.55) | `#8c8c8c` + strikethrough | diff status `Abandoned` |
| `[UNKNOWN_STATUS]` | gray (diff) | (0.55, 0.55, 0.55) | `#8c8c8c` | diff API unavailable |

## NEVER USE

- `green` (#00ff00, #008000, etc.) for any status — not accessible for colorblind users.
- `red` for anything except `[HALLUCINATED]` (which is critical, not just-failed).
- `default link blue` for FAIL — must be orange.

## Banner color rules

- `Trust Gate: PASS` → blue text + bold
- `Trust Gate: SOFT BLOCK` → yellow text + bold
- `Trust Gate: HARD BLOCK` → orange text + bold
- `Trust Gate: BYPASSED` → orange text + bold (force-deliver banner)
- **NEVER** "Trust Gate: PASS" in green.

## Inline span template

```html
<span style="color: #0066cc; font-weight: bold;">[VERIFIED]</span>
```

## Validation regex

Auto-fail any rendered output matching:
```
green|#00[8a-f][0-9a-f]00|#0f0|#00ff00|color:\s*green
```
