# Neovim typing latency in fbcode Python buffers

**Verdict:** the lag was never the network. It was the devserver's Neovim
blocking its main loop ~40–60ms per keystroke, driven by `pyrefly@meta`'s
diagnostics traffic through nvim-treesitter-context. The shipped fix
(`nvim/lua/lib/tscontext-perf.lua`) is a **deliberate hack** — it works, and its
mechanism is only partly understood. This document records what was measured,
what was ruled out, what remains unexplained, and what the proper fix would be.

Status: workaround in place, root cause not fully identified, no upstream bug
filed (see [Filing upstream](#filing-upstream) — there isn't enough to write an
accurate report yet).

---

## Symptom

Editing an fbcode Python file felt laggy; `:enew` felt perfect. Both run through
the same path — headless Neovim on the devvm, TUI on the Mac over an ET tunnel
(`bin/nvs`) — so a constant network cost could not explain the difference.

That observation is what falsified the first hypothesis. An early estimate put
the Mac↔devvm round trip at ~29ms, which would have made the wire the dominant
term. But `:enew` costs 0.5ms server-side, so if the wire really were ~29ms,
`:enew` would feel mushy too. It doesn't. The 29ms figure came from timing a
*fresh TCP connection* through the ET reverse tunnel, which includes ET's
tunnel-open handshake — it measured connection setup, not steady-state RTT. The
true per-keystroke wire cost is well below that and was never the problem.

## Method

`bin/nvim-keystroke-bench`. It runs on the devserver, so it measures main-loop
blocking with no network in the path. It attaches a real UI over msgpack-RPC
(redraw work only happens when a UI is attached), then per sample sends
`nvim_input` immediately followed by `nvim_eval('1')` and times the reply.
Neovim handles those in order, so that reply bounds "keystroke processed and
redraw generated".

An earlier `nvim_eval`-only probe against idle sessions reported p50 0.05ms and
was reported as "server side is free". That was wrong: with no UI attached it
measured whether the main loop was *blocked*, not what a keystroke costs.

> **Safety.** The benchmark types into a buffer, and `lib/autosave.lua` writes on
> `TextChanged`. An ad-hoc early version committed 200 stray `x` characters into
> `fbcode/velox/exec/tests/utils/PlanBuilder.cpp` in checkout1 (reverted). The
> checked-in tool therefore always starts its **own** throwaway server, never a
> live `nvs` session, disables autosave per buffer, undoes its typing, and never
> writes to disk.

## Where the time goes

Server-side only; add your Mac↔devvm RTT for what you actually feel.

| Buffer | p50 | p95 | max |
|---|---|---|---|
| `:enew` scratch | 0.52 ms | 1.07 ms | 1.98 ms |
| `PlanBuilder.cpp` (2878 lines, C++) | 6.04 ms | 17.86 ms | 40.56 ms |
| `velox_auto_merge_tool.py` (2572 lines) | **45–100 ms** | **105–287 ms** | 308 ms |

Peeling layers off the Python file, one at a time:

```
A baseline (5 LSP clients)   p50= 45.45   p95=104.84
B LSP stopped (5 -> 2)       p50=  3.88   p95= 13.36
C  + treesitter off          p50=  1.60   p95=  5.20
D empty scratch buffer       p50=  0.52   p95=  1.07
```

Attributing to individual LSP clients by detaching one at a time:

```
all clients                       p50= 51.84
  minus ids@meta                  p50= 48.52
  minus null-ls                   p50= 39.69
  minus code-compose              p50= 46.51
  minus linttool@meta             p50= 46.85
  minus pyrefly@meta              p50=  8.14   <-- the source
```

`pyrefly@meta` is not slow itself. It publishes **76 `publishDiagnostics`
notifications per 120 keystrokes**, and each fires `DiagnosticChanged`. Four
listeners are registered on that event; removing them one at a time:

```
baseline (4 listeners)              p50= 45.45
  minus satellite_diagnostics       p50= 43.70
  minus treesitter_context_update   p50=  6.37   <-- the entire cost
  minus trouble.diagnostics         p50=  6.23
  minus nvim.diagnostic.status      p50=  6.08
```

C++ is unaffected because `cppls@meta` does not publish diagnostics on every
change; `pyrefly@meta` does.

## Ruled out by measurement

Each of these was hypothesised, tested, and rejected:

| Hypothesis | Result |
|---|---|
| Network RTT | `:enew` is 0.5ms and feels fine; wire estimate was a bad measurement |
| Number of diagnostics | Typing inside a comment (0 diagnostics present) is equally slow |
| Diagnostic flood from transient syntax errors | Same as above |
| `update_in_insert` | Already `false`; forcing it changes nothing |
| Diagnostic rendering (virtual_text / signs / underline) | Disabling all three: no change |
| Semantic tokens | Disabling: no change |
| Inlay hints | Disabling: 46.90 → 41.38 ms, marginal |
| Full-document LSP sync | `textDocumentSync.change == 2` (incremental) |
| URI→buffer churn in the diagnostics handler | Buffer count 3 before and after; URI matches exactly |
| Completion (nvim-cmp) | Disabling after diagnostics: 6.15 → 5.54 ms, marginal |
| Statusline / winbar refresh | Disabling entirely: 44.90 → 39.30 ms, ~12% |
| `force_hl_update` in `render.open` | First fix attempt; **active and did nothing** |

## The unexplained 98%

Direct instrumentation of the two functions in treesitter-context's update path,
over 120 keystrokes:

```
context.get   109.34 ms total across 35 calls
render.open     0.61 ms total across  2 calls
```

Against roughly 5.9 s of measured latency for those keystrokes, that is ~2%. The
remaining ~98% is somewhere inside this subscription's path and **has not been
located**. Candidates not yet eliminated include the `vim.schedule_wrap` /
`throttle_by_id` interaction around `update_win`, and second-order effects on
treesitter's own parsing or on redraw scheduling.

What is solid is the correlation, reproduced across five independent runs and
one same-session A/B: debounce or remove this subscription and p50 goes from
~45–70 ms to ~6–10 ms. The shipped fix rests on that evidence, not on an
explanation.

## What shipped

`nvim/lua/lib/tscontext-perf.lua` adopts the plugin's `DiagnosticChanged`
autocmd, deletes the registration, and re-invokes the captured callback on a
250 ms trailing timer. The plugin's own callback is reused rather than
reimplemented, so its guards and force-highlight behaviour are preserved — only
the timing changes. All other refresh triggers are untouched.

Wired in `nvim/lua/plugins/overrides.lua`; `setup()` returning `false` raises a
`vim.notify` warning rather than degrading silently. Tested by
`nvim/lua/lib/test/tscontext-perf-spec.lua` (storm coalescing, single publish,
idempotent setup, re-adoption after the plugin re-registers, teardown, loud
failure when absent).

Same-session A/B via `nvim-keystroke-bench --compare`:

| File | hack on | hack off |
|---|---|---|
| `velox_auto_merge_tool.py` | p50 **9.92** / p95 25.87 ms | p50 **62.25** / p95 146.34 ms |
| `PlanBuilder.cpp` | p50 5.30 / p95 16.44 ms | p50 5.14 / p95 15.97 ms |

C++ is unchanged, as expected — no benefit, no harm.

Verified the feature still works: the sticky header renders and stays live while
typing (the gutter line number ticks `147 53` → `147 54` mid-run).

## Limitations and risks

- **Diagnostic highlighting inside the 1–5 sticky header lines lags by up to
  250 ms** after you stop typing. Nothing else is deferred.
- **250 ms is a guess** that measured well. Without the mechanism there is no
  principled value.
- **It depends on three private details** of nvim-treesitter-context: the
  augroup name `treesitter_context_update`, that its callback is safe to invoke
  directly, and that its `au_update` reads only `args.event` and `args.match`
  (the args table is synthesised). A plugin update can invalidate any of them.
- **Degradation, not breakage**, if that happens: `setup()` returns `false`, the
  warning fires, and typing latency regresses to the old behaviour.
- The lazy-loaded plugin means the patch is not installed until the first buffer
  is opened. Harmless, but it makes any startup-time probe read `off`.

## The proper solution

In preference order:

1. **Fix it upstream in nvim-treesitter-context.** The right home. Either
   debounce `DiagnosticChanged`, or skip the update when neither the context
   ranges nor the relevant diagnostics have changed. Blocked on closing the 98%
   gap — see below. If that lands, delete `lib/tscontext-perf.lua` entirely.
2. **Reduce `pyrefly@meta`'s publish rate**, if it is publishing more often than
   it needs to. Not investigated; worth a look before assuming the plugin is
   solely at fault.
3. **The supported local alternative:** the plugin's own `on_attach` config hook
   can skip buffers over ~1500 lines. Zero coupling to internals, nothing to
   maintain, fails safe. It costs the sticky header on large files — arguably
   where it is most wanted — which is why it was not chosen, but it is the
   honest fallback if carrying a private patch stops being worth it.

### Filing upstream

Not yet possible to do accurately. A useful report needs the missing 98%: a
profile (`:profile start` around the DiagnosticChanged path, or a
`jit`/`os.clock` harness inside `au_update`) showing which call actually blocks.
Reporting "removing your autocmd makes typing 6× faster, we don't know why"
invites a wontfix.

## Reproducing

```sh
# A/B the workaround in one session, on your own files
~/bin/nvim-keystroke-bench --compare ~/checkout1/fbsource/<some>.py

# unit tests for the workaround
nvim --headless -u NONE --cmd "set rtp+=$HOME/dotfiles/nvim" \
  -c "lua require('lib.test.tscontext-perf-spec').run()" -c "qa!"
```

The benchmark starts and tears down its own Neovim; it never touches a live
`nvs` session and never writes to disk.
