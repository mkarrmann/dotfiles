---
name: omnigent-models
description: >-
  Use BEFORE spawning an Omnigent subagent or child session (sys_session_create,
  sys_session_send with args.model, POST /v1/sessions) whenever a model or
  reasoning effort must be chosen, and whenever asked which Codex or Claude
  models are available here. `sys_list_models` answers empty on Matt's
  deployment; the live per-host catalog comes from `~/bin/omnigent-models`.
  Covers the model ids each harness accepts, the reasoning-effort ladders, and
  why an omitted model does NOT fall back to the configured default. Trigger
  keywords: which models, available models, model id, gpt-6-astra, gpt-5.6-sol,
  opus, sonnet, fable, reasoning_effort, model_override, spawn a codex agent,
  codex subagent, claude subagent, sys_list_models empty.
---

# Omnigent models: what a subagent can run here

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

Output on a personal machine looks like:

```
codex-native models on host fe37… (http://127.0.0.1:6767):
  gpt-6-astra          default  efforts: low medium high xhigh max ultra (default medium)
  gpt-5.6-sol                   efforts: low medium high xhigh max ultra (default low)
  gpt-5.5                       efforts: low medium high xhigh (default medium)
  ...
```

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

An omitted model does not mean "the harness's configured default". For Codex,
Omnigent substitutes its own constant (`gpt-5.6-sol`) unless the agent spec or
the session names one, ignoring the `model =` line in `~/.codex/config.toml`.
Prefer the row marked `default`: that is what the binary itself reports as its
configured default on this host.

## Effort ladders per harness

The script prints the ladder the *binary* advertises. What the agent you spawn
accepts is narrower for the SDK harnesses (omnigent/util/reasoning_effort.py):

| Harness (agent)            | Accepted `reasoning_effort`              |
|----------------------------|------------------------------------------|
| `codex` (SDK agent)        | none minimal low medium high xhigh; `max` and `ultra` fold to `xhigh` |
| `codex-native`             | the printed ladder, incl. `max` / `ultra` |
| `claude-sdk` / `claude-native` | low medium high xhigh max (rows print no ladder) |

## Choosing

- Coding and review work: the `default`-marked Codex model at `high`; drop to
  `medium` for mechanical edits.
- Reasoning-heavy or adversarial review: the top of the accepted ladder.

## Related

- [[omnigent-sessions]]: reading, listing and debugging the session you spawned.
