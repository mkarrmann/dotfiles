#!/usr/bin/env python3
"""Convert markdown [^FN] / [^FN]: ... footnote pairs into HTML <sup>+anchor form
so Google Docs renders them as clickable superscripts + jumpable Citations section.

Also: rewrite known status tags into colorblind-palette HTML spans (HEX-pinned).
Reads:
  sys.argv[1]      draft markdown
  --no-footnotes   degrade to D.5 plain-text annotations (auto-fallback path)
Writes to stdout.
"""

import pathlib
import re
import sys

# HEX-pinned colorblind palette (NEVER green)
TAG_COLORS = {
    "[VERIFIED]": "#0066cc",  # blue (PASS)
    "[LANDED]": "#0066cc",  # blue
    "[STALE]": "#d9a600",  # yellow (PARTIAL)
    "[DRAFT]": "#d9a600",  # yellow
    "[UNVERIFIED]": "#d96600",  # orange (FAIL)
    "[HALLUCINATED]": "#d90000",  # red (critical)
    "[ABANDONED]": "#8c8c8c",  # gray (with strikethrough)
    "[UNKNOWN_STATUS]": "#8c8c8c",
}


def colorize_tag(tag: str) -> str:
    color = TAG_COLORS.get(tag)
    if not color:
        return tag
    style = f"color: {color}; font-weight: bold;"
    if tag == "[ABANDONED]":
        style += " text-decoration: line-through;"
    return f'<span style="{style}">{tag}</span>'


def colorize_all_tags(text: str) -> str:
    """Replace every known status tag in text with HTML span. Skips matches inside <pre>/<code>."""
    # Quick + simple: split on code fences and only process non-code regions.
    parts = re.split(r"(```.*?```|`[^`]+`)", text, flags=re.DOTALL)
    for i in range(0, len(parts), 2):
        for tag in TAG_COLORS:
            parts[i] = parts[i].replace(tag, colorize_tag(tag))
    return "".join(parts)


def main() -> int:
    no_footnotes = "--no-footnotes" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(f"usage: {sys.argv[0]} <draft.md> [--no-footnotes]", file=sys.stderr)
        return 2
    src = pathlib.Path(args[0]).read_text()

    if no_footnotes:
        # D.5 fallback: leave footnote markers alone but colorize tags
        out = colorize_all_tags(src)
        print(out)
        return 0

    # 1. Find all footnote definitions: lines like `[^F3]: ...content...`
    defs = {}
    for m in re.finditer(
        r"^\[\^(F\d+|F-\d+)\]:\s*(.*?)(?=^\[\^|\Z)",
        src,
        flags=re.MULTILINE | re.DOTALL,
    ):
        defs[m.group(1)] = m.group(2).strip()

    # 2. Number them in order of FIRST occurrence in body
    order = []

    def number_inline(m):
        fid = m.group(1)
        if fid not in order:
            order.append(fid)
        n = order.index(fid) + 1
        return f'<sup>[<a href="#cite-{n}">{n}</a>]</sup>'

    # 3. Strip footnote DEFINITIONS from body, keep INLINE markers
    body_only = re.sub(
        r"^\[\^(F\d+|F-\d+)\]:.*?(?=^\[\^|\Z)",
        "",
        src,
        flags=re.MULTILINE | re.DOTALL,
    )
    body_html = re.sub(r"\[\^(F\d+|F-\d+)\]", number_inline, body_only)

    # 4. Append Citations section
    out = body_html.rstrip() + "\n\n## Citations\n\n"
    for n, fid in enumerate(order, 1):
        out += f'<a id="cite-{n}"></a>**[{n}]** {defs.get(fid, "(missing definition)")}\n\n'

    # 5. Colorize status tags throughout
    out = colorize_all_tags(out)

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
