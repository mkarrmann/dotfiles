---
name: omnigent-models
description: >-
  Use BEFORE spawning an Omnigent subagent or child session (sys_session_create,
  sys_session_send with args.model, POST /v1/sessions) whenever a model or
  reasoning effort must be chosen, and whenever asked which Codex or Claude
  models are available here. `sys_list_models` answers empty on Matt's
  deployment; the live per-host catalog comes from `~/bin/omnigent-models`.
  Covers the model ids each harness accepts, the reasoning-effort ladders, the
  `[1m]` Codex twins, and what an omitted model falls back to. Trigger
  keywords: which models, available models, model id, gpt-6.1-sol, gpt-6.2-sol,
  gpt-6-astra, gpt-5.6-sol,
  opus, sonnet, fable, reasoning_effort, model_override, spawn a codex agent,
  codex subagent, claude subagent, sys_list_models empty.
---

# Omnigent models: what a subagent can run here

This applies whichever harness you are: a Claude agent spawning Codex, a Codex
agent spawning Claude, or either spawning its own kind. The spawning tools are
the same for all of them.

`sys_list_models` is empty on this deployment and will stay empty: it lists the
calling agent's declared sub-agent workers (none, for Matt's agents) and reads
their models from the *provider*, which for a CLI subscription login reports
nothing by design. The real answer is the per-host catalog Omnigent probes from
each installed harness binary. One command prints it:

```sh
~/bin/omnigent-models            # codex (the default)
~/bin/omnigent-models claude
~/bin/omnigent-models codex --json   # raw rows, incl. descriptions and tiers
```

Output on a work host (2026-11) looks like:

```
codex-native models on host db80… (http://127.0.0.1:6767):
  gpt-6.1-sol      default  efforts: low medium high xhigh max ultra (default medium)
  gpt-6.1-sol[1m]           efforts: low medium high xhigh max ultra (default high)
  gpt-6.2-sol               efforts: low medium high xhigh max ultra (default medium)
  gpt-5.6-sol               efforts: low medium high xhigh max ultra (default low)
  gpt-5.5                   efforts: low medium high xhigh (default medium)
  ...
```

Pass the plain id, never a `[1m]` twin. Meta's Codex catalog gives a plain Sol
id a 272k window and its `[1m]` twin the full one, on the same deployment;
`model_context_window` in `codex_config/config.template.toml` gives the plain
ids their full window (922k for gpt-6.1-sol and gpt-6.2-sol), so the twins are
redundant.

Run it every time rather than remembering a list. The catalog is probed per
host from that host's binary and login, so it differs between environments
(Astra is available on Matt's personal login and not, as of 2026-09, through the
work gateway) and it refreshes hourly. A child session inherits its parent's
runner, so the host the script defaults to is the host the subagent runs on.

## Spawning with an explicit model

Always pass both fields. Take `model` verbatim from the id column and
`reasoning_effort` from that model's ladder; nothing validates either before the
child's first turn, so a typo surfaces there as a failed turn.

```
sys_session_create(
  agent_id="<codex agent id from sys_agent_list>",
  model="gpt-6-astra",
  reasoning_effort="high",
  title="...", message="...")
```

The `codex` and `claude` SDK agents (harness `codex`, `claude-sdk`) launch the
same binaries as `codex-native` / `claude-native`, so the ids are interchangeable
between the two families. The host route itself accepts only the native names;
the script maps the SDK names for you.

## Why you must name the model

Since Omnigent 0.22 an omitted Codex model reaches Codex unset, and Codex starts
on the `model =` line of the session's `config.toml`: the row marked `default`.
(Through 0.21 Omnigent substituted `gpt-5.6-sol`.) Name it anyway: the default
is per host and follows that file, and for Claude it resolves provider-side,
where you cannot see it.

## Effort ladders per harness

The script prints the ladder the *binary* advertises. What the agent you spawn
accepts is narrower for the SDK harnesses (omnigent/util/reasoning_effort.py):

| Harness (agent)            | Accepted `reasoning_effort`              |
|----------------------------|------------------------------------------|
| `codex` (SDK agent)        | none minimal low medium high xhigh; `max` and `ultra` fold to `xhigh` |
| `codex-native`             | the printed ladder, incl. `max` / `ultra` |
| `claude-sdk` / `claude-native` | low medium high xhigh max (rows print no ladder) |

## Claude ids are family aliases

`omnigent-models claude` prints what Claude Code itself accepts: family
aliases (`opus`, `sonnet`, `haiku`, `fable`, and `[1m]` context variants). Pass
one verbatim as `model` and Claude Code resolves it to that family's current
default, which can move across CLI upgrades. When a fixed model matters, a
canonical id (e.g. `claude-opus-5[1m]`, `claude-sonnet-5`) passes through the
same way and does not float.

## Choosing

- Coding and review work: the `default`-marked Codex model at `high`; drop to
  `medium` for mechanical edits.
- Reasoning-heavy or adversarial review: the top of the accepted ladder.

## Related

- [[omnigent-sessions]]: reading, listing and debugging the session you spawned.
