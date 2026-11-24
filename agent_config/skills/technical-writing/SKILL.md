---
name: technical-writing
description: >-
  Use when writing or revising technical prose meant for humans to read:
  in particular, docs and Workplace posts. Trigger keywords: write
  a doc, design doc, RFC, wiki, README, runbook, post, blog, announcement,
  write-up, draft a doc, draft a post.
---

# Technical writing

Concision, clarity, and appropriate context are king. Everything is in service of this.

You will be tempted to write as if anyone cares about what you write and will put in effort to evaluate it. Catch yourself when you do this, and scold yourself. You are mistaken. No one will ever truly care or ever put in the effort you expect. They will always put in the minimal conceivable effort, and we must structure every aspect of the doc accordingly.

The key point must be easy to identify within 5-seconds with minimal effort (typically due to a clear and concise TL;DR at the top). The doc must be divided into clear sections, and the main point of each section is also immediately clear within 5-seconds with minimal effort. This recurses all the way down through nested sub-sections, depending on the depth for which one reads.

Each section must be short, concise, and clear, for the rare reader who actually reads the sections. These readers are exhausted and we must be empathetic to them. Ideally, each section is several clear, concise bullet points. Paragraphs are used sparingly, and consist of a few easy sentences at most.

None of this is easy. The secret to accomplishing this is to identify the minimal set of white lies and simplifications which helps the reader understand the point we are aiming to get across. This is critical and is the key skill to master, and we must always start by explicitly considering which simplifications we are making. Edge-cases may belong in footnotes, appendices, or your slop doc.

Finally, you must explicitly model and think about the context that the reader has. Typically advice is to assume your readers are smart but have no context. This is awful advice. You must assume tremendous context, else your doc will bloat filling it in. But you should assume no context on what the point you're trying to make is, and you must constantly be filling in that gap. In fact, that's exactly the point of your doc. Other than that, your doc *may* include a glossary or appendix to provide more context (usually not).

## Process

The first step is to create your "slop doc". This is pretty similar to the doc you would usually create. It's super thorough, and includes all the information and caveats that anyone could ever care about. It includes every relevant experiment, piece of analysis, and insights from our discussions.

Next the goal is to start over and create something totally and completely different. Explicitly ask and consider:

a) What's the point of the doc?
b) What context can I and can I not assume the reader has?
c) What simplifications and white lies are we willing to make in order to make the narrative most clear?
d) How to concisely structure the doc to help the reader most easily understand the point?

Explicitly ask yourself and consider these questions. You may or may not ask me specific followup questions to fill in these gaps. Sometimes I want you to try to one-shot the doc, sometimes I want us to treat this as an iterative discussion. Please work with me here.

Then, fill in the gaps of the structure and write the doc. Be brutally concise. If you're not cutting so much that it emotionally hurts, then you haven't even begun the true process. Get to the fucking point and make the point clear. Obsess over that and that alone, and don't let your desire for clever and punchy prose get in your own way.

Finally, re-read only the TL;DR and the headings. If the point doesn't land from those alone, restructure before polishing anything else.

## Where the work lives

Everything starts as local markdown, in `~/writing/<short-slug>/`:

- `slop.md`: the slop doc.
- `doc.md`: the real doc.

Tell me the paths when you create them.

Never publish anywhere yourself (Google Docs, wiki, Workplace, etc.) until I explicitly grant permission to promote that specific doc. Once it's promoted, the Google Doc is the source of truth: keep iterating there, and stop editing `doc.md` unless I say otherwise.

## Other notes

Liberally add links to your doc. Links to justify your claims should go everywhere. Links to justify specific claims and/or figures are amazing. Always do it.

Your links must never link to raw Presto queries/ids (except when you literally need to link to a literal specific Presto query). Instead, create a Daiquery notebook, add your query there, and link to the Daiquery cell. In general, just have empathy for the person reading the doc.

Ensure all figures and tables are clearly labeled, including providing units. Tables must be easily and immediately interpretable without any surrounding context, meaning column and row headers must be self-contained.

## Footnotes

Use the platform's real footnote feature wherever one exists. Never fake one with a superscript number and a "Notes" section at the end. In local markdown, use `[^name]` footnotes.

**Google Docs.** ghtml and the `create`/`replace`/`insert` commands can't make footnotes, so write the doc first, then add each footnote with `meta google.docs batch-update`:

1. Find the index where the reference goes: `meta google.docs get --id=<DOC> --output=raw-json`, then walk `tabs[0].documentTab.body.content` (table cells nest further content) for the text run's `startIndex`. A placeholder you wrote there (e.g. a superscript `1`) makes it easy to find; delete it in the same batch.
2. Create the footnote: `{"createFootnote":{"location":{"index":<N>,"tabId":"t.0"}}}`. The CLI doesn't print the reply, so re-read raw-json and take the new id from `tabs[0].documentTab.footnotes`.
3. Fill it: `{"insertText":{"location":{"segmentId":"<footnoteId>","index":1,"tabId":"t.0"},"text":"..."}}`. The footnote starts as `" \n"`. Insert at index 1, after that space, which is how Docs separates the number from the text.
4. If the text came out superscript (it inherits the style at the reference), clear it: `{"updateTextStyle":{"range":{"segmentId":"<footnoteId>","startIndex":0,"endIndex":<len+1>,"tabId":"t.0"},"textStyle":{"baselineOffset":"NONE"},"fields":"baselineOffset"}}`.

A full-body `replace` deletes the footnotes, so redo these steps after every one. Apply deletions from the end of the doc backwards, so earlier indices stay valid within a batch.

