---
name: omnigent-models
description: >-
  Use BEFORE spawning an Omnigent subagent or child session (sys_session_create,
  sys_session_send with args.model, POST /v1/sessions) whenever a model or
  reasoning effort must be chosen, and whenever asked which models or agents
  are available here, including whether some model can be called at all.
  Neither this skill nor your native subagent tool lists what exists: agents
  come from `sys_agent_list`, models from `~/bin/omnigent-models <agent>`
  (`sys_list_models` answers empty on Matt's deployment). Covers how to read
  both, model id formats, reasoning-effort ladders, and what an omitted model
  falls back to. Trigger keywords: which models, available models, can you
  call, model id, reasoning_effort, model_override, sys_list_models empty, and
  any model or vendor name, e.g. codex, gpt-6.1-sol, opus, sonnet, gemini,
  gemini-4-neon, pi, muse.
---

# Omnigent models: what a subagent can run here

This applies whichever harness you are: a Claude agent spawning Codex, a Codex
agent spawning Gemini, or any agent spawning its own kind. The spawning tools
are the same for all of them.

**This skill is not the list of agents or models.** Every name below is an
example. Matt adds agents and the harnesses' catalogs change underneath, so
read the live sources every time and never answer "that model can't be
called" from memory, from this skill, or from your native subagent tool's
model enum (a Claude Code `Agent` tool offers only Claude models; Omnigent
offers far more).

## The live sources

| Question | Ask |
|---|---|
| Which agents can I spawn, and what is each for? | `sys_agent_list`: the `builtins` rows with `available_on_host: true`, and each row's `description` |
| Which models and efforts does an agent take? | `~/bin/omnigent-models <agent name or harness>` |
| What does an agent run when no model is given? | the `default` row that script prints |
| How is an agent configured? | its spec in `~/dotfiles/omnigent_config/agents/<name>/config.yaml` (Matt's own agents) |

`sys_list_models` is empty on this deployment and will stay empty: it lists the
calling agent's declared sub-agent workers (none, for Matt's agents) and reads
their models from the *provider*, which for a CLI subscription login reports
nothing by design.

`sys_agent_list` output is large; if it spills to a file, read it with `jq` or
a short script, keeping `name`, `agent_id`, `available_on_host` and
`description`.

## Reading the model catalog

```sh
~/bin/omnigent-models                # codex (the default)
~/bin/omnigent-models claude
~/bin/omnigent-models gemini         # any agent name with a spec in dotfiles
~/bin/omnigent-models pi-native      # or a harness name
~/bin/omnigent-models codex --json   # raw rows, incl. descriptions and tiers
```

An agent name resolves through its spec's executor `harness`; that spec's
`model` is marked `default` when the catalog names none. For Codex and Claude
the rows are the per-host catalog Omnigent probes from each harness binary
(refreshed hourly). Omnigent probes no catalog for pi, so for pi harnesses the
script reads this machine's `pi --list-models` instead and prints Omnigent's pi
effort ladder once at the bottom. If a harness has neither, the script exits 1
with the server's reason; say that rather than guessing ids.

The catalog is per host, from that host's binary and login, so it differs
between environments. A child session inherits its parent's runner, so the
host the script defaults to is the host the subagent runs on.

## Model ids by harness (examples)

Take the id verbatim from the script's first column. The formats differ:

- **Codex** (`codex`, `codex-native`): bare ids such as `gpt-6.1-sol`. Pass the
  plain id, never a `[1m]` twin: Meta's catalog gives a plain id a 272k window
  and its twin the full one, while `model_context_window` in
  `codex_config/config.template.toml` already gives the plain ids their full
  window.
- **Claude** (`claude`, `claude-sdk`, `claude-native`): family aliases such as
  `opus` or `sonnet` float to that family's current default across CLI
  upgrades; a canonical id such as `claude-opus-5` does not float.
- **Pi** (the `gemini` agent): `provider/model`, such as
  `google/gemini-4-neon` or `meta/muse-latest`. Pi's login reaches many
  providers through Meta's AI Gateway; the agent's spec pins its default.

The `codex` and `claude` SDK agents launch the same binaries as
`codex-native` / `claude-native`, so ids are interchangeable between the two
families. The host route accepts only native names; the script maps them.

## Spawning with an explicit model

Always pass both fields. Take `model` verbatim from the id column and
`reasoning_effort` from that model's ladder; nothing validates either before the
child's first turn, so a typo surfaces there as a failed turn.

```
sys_session_create(
  agent_id="<agent_id from sys_agent_list>",
  model="<id from omnigent-models>",
  reasoning_effort="high",
  title="...")
```

## Why you must name the model

An omitted model is not reliably the harness default. Since Omnigent 0.22 an
omitted Codex model reaches Codex unset, and Codex starts on the `model =` line
of the session's `config.toml`; for Claude it resolves provider-side, where you
cannot see it. The default is per host and follows config you did not read.

## Effort ladders

The script prints the ladder the *binary* advertises (for pi, the ladder
Omnigent accepts). What an SDK harness accepts can be narrower; the
authoritative per-harness sets are in Omnigent's
`omnigent/util/reasoning_effort.py` (e.g. the `codex` SDK agent folds `max` and
`ultra` to `xhigh`; pi maps `none` to its `off`, clamps `ultra` to `max`, and
clamps any level to the nearest one the model offers).

## Choosing

- Coding and review work: the `default`-marked model at `high`; drop to
  `medium` for mechanical edits.
- Reasoning-heavy or adversarial review: the top of the accepted ladder.
- A different vendor's read: pick an agent on another harness (the agent
  descriptions say which models each reaches).

## Related

- [[omnigent-subagents]]: the launch procedure, briefs and collecting results.
- [[omnigent-sessions]]: reading, listing and debugging the session you spawned.
