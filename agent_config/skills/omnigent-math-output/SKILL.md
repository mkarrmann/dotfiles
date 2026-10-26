---
name: omnigent-math-output
description: >-
  Use BEFORE writing any math, formula, equation, or symbol-heavy explanation
  (LaTeX, KaTeX, variables with subscripts, sums, argmin, fractions) when your
  output is displayed in Omnigent's chat UI (`$OMNIGENT` is set). Omnigent
  renders only `$$…$$`; a single `$…$` is deliberately NOT math and shows as
  raw text, which is what made mixed messages half-broken. Covers the
  delimiter rule, where to put formulas (paragraphs, not tables or bullets with
  single dollars), symbol glossaries, and what to do in a plain terminal.
  Trigger keywords: latex, katex, math, formula, equation, derivation, proof,
  notation, subscript, symbol, variable definitions, glossary. Also covers
  LaTeX in markdown docs viewed in Omnigent's file view (Preview mode,
  multi-line $$ blocks).
---

# Math in Omnigent

Applies whichever harness you are — Claude, Codex, or dvsc — whenever your
message is rendered in Omnigent's chat UI. Check `$OMNIGENT`. If it is unset you
are in a plain terminal: nothing renders, so write math as plain Unicode
(`k′ = μ + z√μ`) or in a code block.

## The one rule: only `$$…$$` is math

Omnigent's Streamdown math plugin is configured with
`singleDollarTextMath: false` (`web/src/components/ai-elements/streamdown-security.ts`),
on purpose: a single `$` is currency or a shell variable far more often than
math. So:

- `$x_1$` → shown literally as `$x_1$`. **Never use single dollars.**
- `$$x_1$$` → rendered.

Every broken message so far had single-dollar math; every message that used
only `$$` rendered fully. Tables and bullets were not the cause.

`\(…\)` and `\[…\]` are rewritten to `$$` only in reasoning blocks, not in
assistant messages. Don't rely on them.

## Layout that is confirmed to render

Display formula on its own line(s):

```
$$
\min_x \sum_g c(D_g, x_g) \quad \text{s.t.} \quad R(x) \ge \tau
$$
```

A symbol definition as its own display block, with the prose on the next line
(no math in the prose):

```
$$\mu_g = \hat g_n(q,j)\cdot\frac{D_g}{\lvert C_j\rvert}$$
Group g's predicted share of the query's true neighbours.
```

## Avoid

- **Single `$` anywhere** (see above).
- **Raw `|` inside math**, e.g. `$$|C_j|$$`: inside tables it splits the cell,
  and it is fragile elsewhere. Use `\lvert C_j \rvert`, `\mid`, `\Vert`.
- **Math inside markdown tables.** Put formulas in paragraphs and keep tables
  to plain text and Unicode.
- **Inline symbols in prose via `$`.** Refer to symbols in prose as plain
  Unicode (μ, ν, τ, k′) or words; put anything with subscripts or structure in
  a `$$` block.

## Markdown files (docs) opened in Omnigent's file view

Formulas in docs are LaTeX too; never fall back to Unicode or code-block math.

- **The view mode matters.** The file view's default **Editor** mode is TipTap with no math
  extension, so it renders no math at all. **Preview** (toolbar view-mode menu, remembered across files)
  uses the same plugin as chat (`web/src/shell/CodeViewer.tsx:119`). Tell the reader to switch to Preview.
- **Display formulas: `$$` alone on a line, the formula, `$$` alone on a line**, with blank lines around.
  A one-line `$$…$$` is *inline* math in remark-math (the opening fence can't have content after it), and
  `\tag{…}` inline is a KaTeX error.
- **Symbols in prose:** inline `$$…$$` (single `$` does not render here either).
- Verified 2026-10-26 by rendering docs through a copy of Preview's exact pipeline (normalize step,
  remark/rehype plugins, sanitize schema, KaTeX).

## Make it interpretable

Formatting is not enough: the reader needs every symbol defined.

- Define every symbol before or right where it first appears, in words, with
  units where they apply.
- For anything longer than a couple of formulas, open with a glossary (one
  display block per symbol, a line of prose under each).
- After each formula, say in one sentence what it means and why it follows.
