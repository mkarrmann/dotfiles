# TODO: keep these skills fresh

These 48 SKILL.md dirs are a **frozen snapshot** of meta-powertools
v1.0.0's skills, copied from `~/.claude/plugins/cache/agent-market/meta-powertools/1.0.0/skills/`
on 2026-06-28. They were vendored so we could drop the
meta-powertools → 10x-data-scientist bundle without losing the useful
skill instructions.

## The staleness problem

`agent-market`'s 2-hour systemd cron keeps the *upstream* plugin cache
fresh, but only if the plugin is installed. We uninstalled it, so the
cache disappears and there's nothing to diff against — these copies
just drift further from upstream forever.

## Refresh options to evaluate

1. **Reinstall → rsync → uninstall script.** Periodically:
   `agent-market plugin meta-powertools install --agent claude && \
    rsync -a --delete ~/.claude/plugins/cache/agent-market/meta-powertools/*/skills/$NAME/ ~/dotfiles/agent_config/skills/meta-powertools-vendored/$NAME/ && \
    agent-market plugin meta-powertools uninstall --agent claude` (per skill).
   Heavy (touches the registry every run) but cleanest.

2. **Symlink to cache, force the install.** Symlink each skill dir to
   the cache instead of copying. Requires meta-powertools to stay
   installed, which we'd need to disable via `enabledPlugins=false` in
   settings.json — only works if Claude honors `false` as "skip
   loading" (untested). If it does, this is the ideal answer.

3. **Sparse checkout from upstream source.** The skills live in
   `fbcode/claude-templates/components/plugins/meta-powertools/skills/`.
   A weekly cron could `eden prefetch` + `rsync` from there directly,
   bypassing the agent-market cache entirely. No package-manager
   roundtrip, but ties us to an fbsource checkout.

4. **Just live with the staleness.** Skills evolve slowly. Refresh
   manually every few months when something feels off.

## Picking one

Probably (2) if `enabledPlugins=false` actually works — verify next
session. Falling back to (1) as a once-a-week cron. (4) is the
zero-effort answer if you accept the drift.

The MCPs in `plugins/custom-mcps/mcps/` have the same problem in
principle (e.g. the upstream scuba.json gained a smarter dispatcher
recently). Apply the same approach.
