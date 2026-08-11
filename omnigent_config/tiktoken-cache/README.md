# Vendored tiktoken BPE cache

`TIKTOKEN_CACHE_DIR` points here. Without it, every Omnigent process that
counts tokens dies on this devserver.

## Why this exists

Omnigent estimates context usage with `tiktoken` (`omnigent/runtime/compaction.py`
→ `count_tokens`, reached from the REPL's resume path and from the runner's
`/v1/summarize`). `tiktoken` ships no encoding data — it downloads the BPE from
`openaipublic.blob.core.windows.net` on first use and caches it on disk.

That host does not resolve from a Meta devserver, directly or through
`fwdproxy:8080`. So the first `count_tokens` call raises `NameResolutionError`,
and because the resume call site is unguarded, it aborts the whole attach:

    Failed to resume e90d8852f2c74740…: HTTPSConnectionPool(host=
    'openaipublic.blob.core.windows.net', port=443): Max retries exceeded

Still unfixed upstream as of omnigent 0.9.0 (verified against the published
wheel). Seeding the cache is the only way to make it work offline.

## The file

`9b5ad71b2ce5302211f9c61530b329a4922fc6a4` is `cl100k_base.tiktoken`, named for
tiktoken's cache key — `sha1()` of the download URL (`tiktoken/load.py`,
`read_file_cached`). The name must stay exactly as-is.

Provenance is pinned by content, not by source: tiktoken asserts
`sha256 = 223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`
for this blob in `tiktoken_ext/openai_public.py`, and this copy matches. Verify
any time with:

    sha256sum ~/dotfiles/omnigent_config/tiktoken-cache/9b5ad71b2ce5302211f9c61530b329a4922fc6a4

`cl100k_base` is the only encoding needed: `count_tokens` calls
`encoding_for_model()` first, and every Claude id raises `KeyError` there and
falls back to `cl100k_base`.

## How it reaches each process

`TIKTOKEN_CACHE_DIR` is set in four places because no single one covers them
all:

| Consumer | Wired by |
|---|---|
| `omnigent` REPL / CLI in a shell | `.shell_env` |
| systemd `--user` units | `~/.config/environment.d/omnigent.conf` (written by `sync.sh`) |
| `omnigent-host` / `omnigent-server` | explicit `Environment=` in their units |
| the **runner** (where `count_tokens` actually runs) | `OMNIGENT_RUNNER_ENV_PASSTHROUGH` in `omnigent-host.service` + the Mac plist |

That last row is the easy one to miss. The host filters its environment through
a hardcoded allowlist before spawning a runner (`omnigent/host/connect.py`,
`_build_runner_env`); `TIKTOKEN_CACHE_DIR` is not on it, so setting the var on
the host alone does nothing. `OMNIGENT_RUNNER_ENV_PASSTHROUGH` unions into that
allowlist, so the name has to be listed there too.

## Untracked writes

tiktoken treats this as a real cache and will write other encodings here if it
ever manages to fetch one. `.gitignore` tracks only the vetted blob.
