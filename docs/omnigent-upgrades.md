# Upgrading Omnigent

The installed Omnigent is the published PyPI wheel, managed by
`bin/omnigent-version-ensure` (run from `init.sh`). Nothing runs from
`~/repos/omnigent`.

> **Never run `omni update` / `omnigent update` on any machine here.** It
> terminates whatever process holds `:6767`, which on a hub is
> `omnigent-server.service` and on the Mac is mac_proxy — neither of which it
> can tell apart from the stray local server it thinks it is cleaning up. Use
> `./init.sh`, or the explicit `uv tool install` below. See
> "`omni update` takes the hub down (2026-09-21)".

## The floor

`omnigent_config/topology.env` carries `OMNIGENT_MIN_VERSION`. It is a **floor,
not a pin**:

- below the floor → rolled forward to the latest release
- at or above it → left alone, so a machine you deliberately moved ahead stays
  ahead
- missing a `--with` extra → the _currently installed_ version is reinstalled,
  never silently bumped

## Roll the fleet forward

1. Bump `OMNIGENT_MIN_VERSION` in `omnigent_config/topology.env`, commit, push.
2. Upgrade the Mac and the standby, then the hub last. On each machine:

   ```bash
   cd ~/dotfiles && git pull && ./init.sh
   systemctl --user daemon-reload
   ```

   **The host daemon restart is automatic.** `omnigent-version-ensure` does it
   whenever it actually writes the package — `try-restart` on Linux, stop plus
   `launchctl kickstart -k` on macOS — and does nothing on a converge that
   installs nothing. It has to: a daemon keeps running the code it started
   with, and its runners fork from a zygote holding old modules in memory, so
   after an in-place upgrade a fork mixes those with the new code on disk and
   dies. That is not a broken install and does not read like one — see the
   2026-08-27 entry under "Known upstream bugs".

   **The `chat.db` backup is automatic too.** On the host that owns the shared
   database (`omnigent-hub gate`), `omnigent-version-ensure` takes an
   `omnigent-hub snapshot` before replacing the package — a consistent SQLite
   backup that also covers the bridge and watcher databases and artifacts — and
   refuses to upgrade if it cannot. Override deliberately with
   `OMNIGENT_SKIP_RECOVERY_POINT=1`. Do not `cp chat.db` by hand: a copy of a
   live database with a WAL is not a consistent backup.

3. **Restart `omnigent-server` on the hub by hand.** It is deliberately not
   restarted automatically: a release can migrate `chat.db`, so when that
   happens is the operator's call. First read init's output for
   `requests OMNIGENT_FEATURES ... which this release does not define` — an
   unknown feature name in `systemd/omnigent-server.service` makes the server
   refuse to start, so fix the unit before restarting. Then:

   ```bash
   systemctl --user stop omnigent-server
   systemctl --user start omnigent-server
   ```

4. Verify (from `/` — `python -c` puts cwd on `sys.path`, so running this inside
   an omnigent checkout tests the wrong copy). The `import litellm` line is
   load-bearing: the printed numbers alone stopped proving the extra is there,
   for the reason under "Do not forget `--with litellm`".
   ```bash
   cd / && LITELLM_LOCAL_MODEL_COST_MAP=True \
     ~/.local/share/uv/tools/omnigent/bin/python -c "
   import litellm
   from omnigent.llms.context_window import get_model_context_window as g
   print(g('claude-haiku-4-5'), g('gemini-2.5-pro'))"   # expect 200000 1048576
   ```
   Then run `bin/omnigent-onboard-check` on each Linux machine, and send one
   message in any session: that is the only check that exercises a runner on
   the new code.

The hub goes last because clients tolerate an older server better than the
reverse.

## LiteLLM import race breaks HTTP responses (2026-10-08)

Omnigent 0.17.0 resolves model context metadata in worker threads. The first
lookup can import LiteLLM while Uvicorn is already serving requests. LiteLLM
1.104.0 attaches redaction filters to Uvicorn's loggers before its own import
finishes. A concurrent access log imports the filter's dependencies, producing
an import-lock cycle: the worker owns `litellm` and waits for
`litellm.types.secret_managers.main`, while the logging thread owns the latter
and waits for `litellm`.

Python aborts the import with `_DeadlockError` and removes the incomplete root
module. The registered filters survive and subsequently raise
`KeyError: 'litellm'`. Uvicorn logs before writing HTTP headers, so requests
receive no response even though the server process is alive. Omnigent swallows
the original worker exception. Restarting briefly restores health, then the
next cold import can trigger the race again.

`systemd/omnigent-server.service` uses `bin/omnigent-server-run` to complete the
public `import litellm` before running the installed Omnigent console script
in the same process. It retains redaction, uses the existing uv-tool interpreter,
and reports initialization failures before accepting requests. No dependency
pin or edits to the installed package are needed. Remove the launcher when
upstream makes concurrent initialization safe, after checking the regression.

Verify without running Omnigent or touching its databases:

```bash
python3 -B -m unittest discover -s tests -p test_omnigent_server_run.py
```

The checks cover initialization order, argument and exit-status forwarding,
failure before console entry, and concurrent HTTP responses through real
Uvicorn with the installed LiteLLM filters. The HTTP check starts and closes
its own temporary test server on an unused loopback port.

Activation is separate from editing the source. Once ready to interrupt hub
connections, reload systemd's unit definitions and restart the server:

```bash
systemctl --user daemon-reload
systemctl --user restart omnigent-server.service
curl --noproxy '*' --max-time 5 http://127.0.0.1:6767/health
```

## Move one machine ahead of the floor

```bash
uv tool install --force omnigent --with litellm
```

`--with litellm` is not optional — omit it and the next `init.sh` notices the
missing extra and reinstalls. `init.sh` will leave the newer version in place.

## Do not forget `--with litellm`

Omnigent resolves context windows through, in order: its own small registry →
litellm → the MLflow catalog → a 128k default. litellm is an optional dependency
it does not ship, and the catalog fetch fails here — so without it **every model
the registry does not name falls back to 128k**, mis-sizing the context ring and
the compaction threshold. Verified present in 0.6.0 and 0.9.0; re-check the
resolution order if a future release restructures
`omnigent/llms/context_window.py`.

**Check the import first; the numbers depend on the release.** Through 0.9.0
the fallback caught everything, which made a context-window print a reliable
canary. On 0.14.0 the registry named the models we actually run, so
`g('claude-opus-5'), g('gpt-5.5')` printed `1000000 1050000` with litellm
absent and masked the loss. Measured 2026-09-21, after a bare
`uv tool install omnigent@latest` silently dropped the extra — `uv tool install`
rebuilds the receipt from its arguments, so omitting `--with litellm` uninstalls
litellm and its 18 transitive packages rather than preserving them. On 0.17.0
the registry no longer covers those models and every model falls back to 128000
without litellm (measured 2026-10-06), so the numbers are a canary again.
`import litellm` is the check that holds on every release.

Pick canary models that litellm's bundled cost map still knows. A model
reporting 128000 _with_ litellm present may simply be unmapped: by 0.17.0
litellm raises `ModelNotMappedError` for the retired `claude-3-5-sonnet-*` ids,
so they report 128000 regardless.

## Check after any upgrade

These are things upstream has broken or could break, none of which fail loudly:

- **Context windows** — the verify command above. Also confirm
  `omnigent/llms/context_window.py` still consults litellm.
- **tiktoken** — `omnigent_config/tiktoken-cache/README.md`. The vendored blob
  is keyed by a sha1 of the download URL; if upstream changes encodings, a new
  blob is needed.
- **Runner env** — `omnigent/host/connect.py` filters the runner's environment
  through a hardcoded allowlist. `TIKTOKEN_CACHE_DIR` and
  `LITELLM_LOCAL_MODEL_COST_MAP` ride `OMNIGENT_RUNNER_ENV_PASSTHROUGH` in
  `systemd/omnigent-host.service`; if that mechanism changes, the runner
  silently loses both.
- **Provider block** — `providers.vertex-claude` in `config.shared.yaml` must
  still parse (`kind: subscription`).

## `omni update` takes the hub down (2026-09-21)

`with-proxy omni update`, run on both devservers, took the fleet off Omnigent
for about five hours (15:59–21:18). It is not an upgrade path this setup
supports and nothing in dotfiles invokes it.

`omni update` → `_drain_and_stop_local_server` → `stop_untracked_local_server`
(`omnigent/host/local_server.py`) probes `/health` on the canonical port, asks
`lsof` which pid is listening, and terminates it. No pidfile match, no ownership
check, no awareness of systemd or launchd. Its docstring frames it as sweeping
up an orphan whose pidfile was lost — it cannot tell an orphan from a supervised
hub. Upstream assumes the single-machine model where the CLI owns the local
server; here `:6767` is a shared hub, or mac_proxy relaying to one.

- **Mac** — kills mac_proxy. `KeepAlive` in
  `launchd/com.mkarrmann.omnigent-tunnel.plist` makes it a sub-second gap. That
  plist and `sync.sh` already carried this warning; both only covered the Mac.
- **Hub devserver** — kills `omnigent-server.service`. `Restart=always` brings
  it back after `RestartSec=5`, straight into the package tree `uv tool upgrade`
  is still rewriting (that upgrade spent 7.3s just uninstalling). The unit fails,
  retries, exhausts its start limit, and stays dead until someone looks.

The tell from a client: the host daemon loops on `Host tunnel disconnected: did
not receive a valid HTTP response. Reconnecting in 3.0s` while
`curl --noproxy '*' http://127.0.0.1:16767/health` resets the connection — the ET
forward is healthy and nothing is listening on the far side. `omnigent host
status` shows `host=unknown` with `ReadError: [Errno 54] Connection reset by
peer`.

Recovery is `systemctl --user reset-failed omnigent-server` then
`systemctl --user start omnigent-server`, once the package tree has settled.

Note what else this costs: the hubs went 0.13.0 → 0.14.0 without a `chat.db`
backup, because nobody chose that upgrade or knew it was happening. An
unplanned hub upgrade skips the one step that protects the shared database —
which is why `omnigent-version-ensure` now takes the recovery point itself, but
only on the path it controls; `omni update` bypasses it.

## Stale daemons after an in-place upgrade (2026-08-27)

One upgrade produced three separate outages, none of which looked like an
upgrade problem. `uv tool install --force` replaces the package tree under
every already-running process; the daemons keep their imported modules, and
runners fork from a zygote inside them. A fork then mixes old in-memory modules
with new on-disk ones:

- Mac, daemon 3 days old — `ValueError: runner fork request requires a cwd`
- FTW, daemon 8 days old — `ImportError: cannot import name
'RUNNER_SLICE_KEY_ENV_VAR' from 'omnigent.runner.identity'`

Both read as a corrupt install. Neither was: the symbol was present on disk and
every file came from the same install. The tell is a traceback whose frames
point at docstrings and comments — old compiled line numbers rendered against
new source.

CCO escaped only because its daemon happened to restart 13 minutes after the
upgrade. Now handled automatically by `omnigent-version-ensure`; the note here
is for reading old logs and for recognising the shape if it appears elsewhere.

Related trap, fixed the same day: `omnigent stop` terms the host's tmux pane
but the session outlives it, and `tmux has-session` still succeeds — so
`omnigent-host-ensure` saw a session, declined to start one, and left the host
offline permanently. It now requires a pane that is not dead.

## Known upstream bugs (unfixed as of 0.9.0)

- `_is_context_overflow_error` fabricates `128000, 128001` when the upstream
  message carries no digits — a made-up number that reads like real data.
- The REPL's resume path calls `count_tokens` unguarded, so a tokenizer failure
  aborts the whole attach rather than just skipping the meter.
- `/compact` on a `claude-sdk` session requires `llm.model` / `executor.model`
  _and_ an api-key-style provider. Every Meta endpoint is mTLS and omnigent's
  LLM client has no client-cert support, so server-side compaction cannot work
  here at all.
