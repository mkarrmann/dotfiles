# Muse Code in Omnigent

How Meta's internal Muse models reach Omnigent, and what changes when the
Muse Wonder models (Wonder, Wonder Full) become available internally.

## Path

```
CodeCompanion → Omnigent `muse` agent (harness acp:muse-core)
  → ~/bin/dvsc-core-acp --default-mode muse      (users/mk/mkarrmann/dvsc-core-acp)
  → dm-core server, agentType Muse               (dmt; dm-core/src/extension-host/muse)
  → /usr/local/bin/muse serve --trust-workspace  (managed Meta launcher, fb-muse-code RPM)
  → AI Gateway, devmate-modelapi upstream (Meta Model API)
```

- **Internal, not public.** dm-core runs `muse` from PATH, which resolves to
  the managed launcher. That launcher brings Meta identity, managed
  hooks/plugins/policy, and the AI Gateway route. dm-core's own comment
  (`MuseExecutable.ts`): "The PATH launcher applies Meta's sandbox and network
  setup; muse.real bypasses it." The public build (`dev.meta.ai/install.sh`,
  API key, `api.meta.ai`) is not installed and must stay off PATH.
- **Sandbox.** Under dm-core the launcher runs
  `muse.real serve --disable-sandbox`. This matches dvsc's no-approval stance
  (`DVSC_AUTO_RUN_MODE=autoRunAll`, `permission_mode: bypassPermissions`).
- **Why a mode, not a new wrapper.** dm-core already maintains an MSP client
  for Muse, owned by the codecompose oncall. A separate ACP↔MSP wrapper would
  have to track MSP, which changes nearly daily. The wrapper needed one new
  mode plus `--default-mode`, because Omnigent's generic ACP harness forwards
  a model but no mode.
- **Gates.** Two must pass: dm-core's `devmate_muse_harness`, and per-model
  Model API access (Muse Wonder 1.0 Internal is gated by
  `cpe_muse_wonder_access`).

## Models

Pass catalog ids only. dm-core rejects the CLI's `muse-latest` alias with
`Muse model 'muse-latest' is not available`.

- `muse-spark-1.3-internal`: the default; works end to end.
- `tbh-1015`: the current id behind `muse-latest`. dm-core accepted it but
  produced an empty turn in testing, so the picker omits it.

## Verifying a session really ran on Muse

The wrapper writes one dm-core log per session:
`/tmp/dvsc-core-acp-<sessionId>.log`. The session id appears in the wrapper's
`new_session` log line (`~/.cache/dvsc-core-acp.log`). Muse sessions contain:

```
Muse on a developer host, upstream https://devmate-modelapi.ai-gateway.fbinfra.net/v1: dev-infra
stderr: Muse Code at Meta (https://fb.workplace.com/groups/27315719428107177)
stderr: Using AI Gateway (Meta Model API upstream)
```

## Activation

These steps change live state, so run them deliberately.

1. Make the new dist reachable. `bin/dvsc-core-acp` takes the first built dist
   in the order checkout1..4, then `~/fbsource`. Rebuild in whichever checkout
   wins, from source that includes the muse mode:
   `cd <checkout>/fbsource/users/mk/mkarrmann/dvsc-core-acp && npx tsc -b`.
   If that dist predates the change, the launcher refuses `--default-mode`
   instead of silently running native.
2. `~/dotfiles/bin/omnigent-dvsc-ensure --config-only` adds the `muse-core`
   entry to the `acp:` block of `~/.omnigent/config.yaml`. The slug must not
   be `muse`: the server seeds a generic agent named after each acp slug at
   boot, which would replace the `muse` spec.
3. `~/dotfiles/bin/omnigent-agents-ensure` registers the `muse` builtin on the
   active hub. It restarts the server only when the hub is quiescent.

## When Muse Wonder lands

1. Check whether you have it:
   `muse exec --model <id> "Reply with exactly: PONG"`, and whether it appears
   in `model/list`.
2. Add the catalog id to `OMNIGENT_MODELS.muse` in
   `nvim/lua/plugins/codecompanion.lua`, and add its context window to the
   wrapper's `models.ts`.
3. If dm-core's Muse catalog lags the CLI, that is dm-core's dependency. File
   it against codecompanion rather than bypassing dm-core.

What would change this plan:

- **Muse adds ACP itself.** One `acp.agents` entry pointing at `muse` would
  replace the dm-core hop.
- **dm-core drops Muse.** Fall back to a direct `muse serve` (MSP) wrapper.

## Known gaps

- The wrapper ignores stdin EOF, and SIGTERM leaves its dm-core subprocesses
  running. This predates Muse and affects every mode; there is a TODO in
  `acp-wrapper/src/index.ts`.
- dm-core allows Muse only for a root session, so Muse can't run as a dm-core
  subagent.
- `dmt models` does not list a `muse` harness even though Muse runs.
- Effort is not forwarded, so dm-core's Muse default applies.
