# Upgrading Omnigent

The installed Omnigent is the published PyPI wheel, managed by
`bin/omnigent-version-ensure` (run from `init.sh`). Nothing runs from
`~/repos/omnigent`.

## The floor

`omnigent_config/topology.env` carries `OMNIGENT_MIN_VERSION`. It is a **floor,
not a pin**:

- below the floor → rolled forward to the latest release
- at or above it → left alone, so a machine you deliberately moved ahead stays
  ahead
- missing a `--with` extra → the *currently installed* version is reinstalled,
  never silently bumped

## Roll the fleet forward

1. Bump `OMNIGENT_MIN_VERSION` in `omnigent_config/topology.env`, commit, push.
2. On the **hub first**, back up the database — the hub and standby share it and
   a new release may migrate the schema:
   ```bash
   systemctl --user stop omnigent-server
   cp ~/.omnigent/chat.db ~/.omnigent/chat.db.pre-<version>
   ```
3. On each machine:
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

   **`omnigent-server` on the hub is still manual**, deliberately: a release
   can migrate `chat.db`, so step 2's backup must happen first.
   `omnigent-version-ensure` prints the exact commands when it sees a running
   server on pre-upgrade code.
4. Verify (from `/` — `python -c` puts cwd on `sys.path`, so running this inside
   an omnigent checkout tests the wrong copy):
   ```bash
   cd / && LITELLM_LOCAL_MODEL_COST_MAP=True \
     ~/.local/share/uv/tools/omnigent/bin/python -c "
   from omnigent.llms.context_window import get_model_context_window as g
   print(g('claude-opus-5'), g('gpt-5.5'))"   # expect 1000000 1050000
   ```

Upgrade the hub last if you care about uptime: clients tolerate an older server
better than the reverse.

## Move one machine ahead of the floor

```bash
uv tool install --force omnigent --with litellm
```

`--with litellm` is not optional — omit it and the next `init.sh` notices the
missing extra and reinstalls. `init.sh` will leave the newer version in place.

## Do not forget `--with litellm`

Omnigent resolves context windows through, in order: its own small registry →
litellm → the MLflow catalog → a 128k default. litellm is an optional dependency
it does not ship, and the catalog fetch fails here — so without litellm **every
model silently reports 128k**, mis-sizing the context ring and the compaction
threshold. Verified present in 0.6.0 and 0.9.0; re-check the resolution order if
a future release restructures `omnigent/llms/context_window.py`.

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
  *and* an api-key-style provider. Every Meta endpoint is mTLS and omnigent's
  LLM client has no client-cert support, so server-side compaction cannot work
  here at all.
