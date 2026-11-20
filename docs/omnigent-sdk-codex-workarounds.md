# Temporary SDK Codex workarounds

The `HACK(omnigent-sdk-codex-cwd)` workaround that makes the Omnigent `codex`
agent run in the session workspace, and the checklist for removing it.

> **HACK STATUS (2026-07-20): active for Omnigent 0.5.1.** The local Omnigent
> `main` checkout at `0beca0bb814b99601adf35fc3a271f5a8b3f8f6b` still has
> the same defect. This doc is a removal checklist, not a supported design
> to preserve indefinitely.

## Managed-host session cwd is dropped by several headless spawn paths

Omnigent clients already send the correct absolute `workspace` in
`POST /v1/sessions`. The server validates it, the host launches a dedicated
runner with `OMNIGENT_RUNNER_WORKSPACE=<workspace>`, and the runner passes that
runtime cwd into `_resolve_harness_config`. The observed failure was Codex, but
the break is a broader gap inside Omnigent:

1. `omnigent/runner/app.py::_build_spawn_env_from_spec` receives `cwd`, but its
   built-in `claude-sdk`, `codex`, `cursor`, and `copilot` branches call their
   spawn-env builders without it. The `pi`, `kimi`, and plugin-builder branches
   do pass `cwd`.
2. Those four builders have no `cwd` parameter and never set their respective
   `HARNESS_CLAUDE_SDK_CWD`, `HARNESS_CODEX_CWD`, `HARNESS_CURSOR_CWD`, or
   `HARNESS_COPILOT_CWD` variable.
3. `omnigent/inner/codex_harness.py` reads only `HARNESS_CODEX_CWD` for the
   executor's explicit cwd. It otherwise receives the agent's raw
   `HARNESS_CODEX_OS_ENV`, whose cwd is the placeholder `.`.
4. `CodexExecutor` launches app-server and sends thread/start that relative
   cwd from the runner process's inherited directory (`$HOME` on the managed
   host), not from the session workspace.

The other affected harnesses have the equivalent fallback shape: their explicit
cwd env var is absent and their serialized `os_env.cwd` is still `.`. Omnigent
correctly resolves the same runner workspace for `sys_os_*` resources and native
terminal harnesses. Therefore the upstream bug is not Codex-only; this
dotfiles workaround is Codex-specific because Codex is the harness investigated
and its external launcher gives us a contained interception point.

The current built-in headless harnesses fall into three groups:

- **Affected by the dropped runtime cwd:** `claude-sdk`, `codex`, `cursor`, and
  `copilot`. Installed 0.5.1 produces no corresponding `HARNESS_*_CWD` for all
  four while serializing `os_env.cwd` as `.`.
- **Already recover the workspace:** `pi` and `kimi` receive `cwd` in their
  spawn-env builder; `qwen`, `goose`, `hermes`, and generic `acp` explicitly
  fall back to `OMNIGENT_RUNNER_WORKSPACE` inside their harness wrapper.
- **Not the same cwd contract:** native terminal harnesses launch directly in
  the runner workspace. `openai-agents` and `antigravity` do not expose the
  same workspace-backed CLI/native-tool surface.

The dotfiles workaround deliberately crosses three configuration layers:

- `systemd/omnigent-host.service` pins `HARNESS_CODEX_PATH=%h/bin/codex` so
  Omnigent cannot bypass the dotfiles launcher by discovering
  `/usr/local/bin/codex` directly.
- `omnigent_config/agents/codex/config.yaml` allowlists only the non-secret
  `OMNIGENT_RUNNER_WORKSPACE` variable through CodexExecutor's environment
  scrub.
- `bin/codex` validates that value and `cd -P`s before executing app-server.
  The raw `os_env.cwd: .` then resolves relative to the correct process cwd.

## Native alternatives checked

There is no equivalent supported per-session configuration in Omnigent today:

- `HARNESS_CODEX_CWD` is supported but static in the host environment. The
  host daemon starts before any session workspace exists, and systemd does not
  substitute one environment variable into another per child runner.
- `os_env.cwd` is a static agent boundary/placeholder. A single registered
  agent cannot set it to a different absolute checkout for every session.
  `cwd: ${OMNIGENT_RUNNER_WORKSPACE}` is not an escape hatch: both
  `parse(..., expand_env=True)` and `expand_env=False` leave that field
  literal, as `_parse_os_env` does not expand cwd values.
- `os_env.sandbox.env_passthrough` can allow an existing variable through; it
  cannot assign `HARNESS_CODEX_CWD` from `OMNIGENT_RUNNER_WORKSPACE`.
- `SessionCreateRequest` exposes `workspace` but no per-session environment or
  harness-cwd override. `executor.config` has no supported Codex cwd field.
- A community harness plugin cannot override the built-in `codex` harness id;
  inventing a second custom harness would reproduce core Codex integration and
  be a larger workaround.
- `codex-native-ui` starts in the proper workspace, but it is a terminal-backed
  native session rather than the streamed SDK chat surface used here.

## Upstream fix and cleanup

The upstream fix should make runtime cwd part of the built-in headless-harness
contract, not patch Codex alone:

1. Add `cwd: Path | None` to the affected built-in spawn-env builders.
2. Pass the already-resolved `cwd` from `_build_spawn_env_from_spec`.
3. Set each harness's `HARNESS_*_CWD` when present, with parametrized managed-host
   regression tests for agents declaring `os_env.cwd: .`.

For Codex specifically, that means calling
`_build_codex_spawn_env(spec, cwd=cwd, workdir=workdir)` and emitting
`HARNESS_CODEX_CWD=str(cwd)`.

After a released Omnigent version passes a live check that the actual Codex
app-server `/proc/<pid>/cwd` and persisted `sys_os_shell.cwd` both equal the
session workspace **without the workaround**:

1. Remove the `OMNIGENT_RUNNER_WORKSPACE` block and test-only
   `OMNIGENT_CODEX_REAL_PATH` seam from `bin/codex`, then remove the
   workspace-specific wrapper test file.
2. Remove `OMNIGENT_RUNNER_WORKSPACE` from the Codex agent's
   `env_passthrough`.
3. Remove `HARNESS_CODEX_PATH` from `omnigent-host.service` and from
   `OMNIGENT_RUNNER_ENV_PASSTHROUGH` unless another launcher requirement still
   needs it.
4. Re-run `omnigent-agents-ensure`, restart the host, and repeat the live CWD
   check in both numbered checkouts and concurrent sessions.

The adjacent `HARNESS_CODEX_DISABLE_NATIVE_TOOLS=1` setting addresses a
separate issue: native Codex tool calls are not emitted as Omnigent progress or
durable tool items, which caused the false 240-second idle-watchdog failure.
Remove it only after Omnigent forwards native tool activity and cancellation
correctly; it is not part of the CWD cleanup. (A companion
`HARNESS_TURN_TIMEOUT_S=1800` override was dropped once Omnigent's own default
rose to 3600.)
