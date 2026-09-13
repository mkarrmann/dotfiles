---
name: waiting-without-polling
description: >-
  Use before waiting for a build, test, CI result, job, deployment, or other
  changing condition, and when asked to monitor, babysit, or report back.
  Prefer harness-native completion notifications, monitors, and event
  subscriptions over repeated model status checks. Explains when Omnigent's
  persistent watcher or specialized Phabricator feedback is useful.
---

# Waiting without polling

## Prefer notification over repeated model check-ins

Arrange for useful information to wake the agent, then continue other work or
finish the turn. Avoid cycles of status tool calls, sleeps, and model turns
that only discover "still running". Moving those same model checks into a
scheduled prompt does not remove the waste.

A quiet script can poll mechanically without involving the model. Prefer
upstream events when available; otherwise let code compare state and notify
only when action may be needed. Setup and wake turns still use tokens, and
the observer still consumes compute/API calls. Savings depend on the checks
avoided, context size, and caching; there is no universal token cost per turn.

## Choose the mechanism that fits

**Harness-native equivalents are encouraged.** Prefer an existing completion
notification, background task, monitor, or event channel when it covers the
required information, lifetime, execution host, and latency. Do not require
Omnigent's custom tool simply because it is installed.

| Situation | Suitable approach and why |
| --- | --- |
| A build or test the agent just launched | Native background execution with completion notification; process exit already provides the signal. |
| A file, log, or external job during an open session | Native monitor/event channel, or a bounded quiet script that notifies on completion; no model check-ins while unchanged. |
| A producer already emits a webhook or event stream | An available native channel/integration; use the producer's signal instead of adding status polling. |
| An external job may outlive the harness process | Omnigent `watch-anything`, when the probe and its documented limitations fit; persistent requests can later wake the same usable Omnigent session. |
| Submitted Meta diffs need CI and review follow-up | Use `phabricator-diff-watch` by default. A generic notification tool alone does not establish CI/review coverage. Follow that skill's evidence requirements before substituting another integration. |
| Deciding when to act itself requires judgment | Propose a bounded scheduled model pass such as `/loop`; use it when a mechanical notification cannot answer the question, not as a substitute for an available completion event. |

The extra value of Omnigent's watcher is persisted requests and pending delivery
through the session server, plus specialized event sources. It is useful when
native facilities are absent or tied to a shorter-lived process. Merely ending
a turn is not enough reason to choose it: native background work can survive
final responses. Check whether the observer survives the actual harness
exit/resume boundary.

Omnigent's generic tool polls sampled command output; it is not a universal
event bus. It supports recurring changes after acknowledgment, but sampling
and batching can miss intermediate transitions. Its current defaults are a
60-second nominal poll interval (30-second configured minimum, ±10% jitter),
five-minute batching, and a ten-minute gap between session notifications; busy
or unreachable sessions can defer them further. It retires after seven days
without a session delivery, even with deferred feedback, and needs the same
usable Omnigent session. See [[watch-anything]] for probe, retry, downtime,
and status limitations before relying on that lifetime.

## Harness examples

### Claude Code

- For a launched build/test or a bounded condition script, use Bash's
  `run_in_background: true` when available; completion produces a notification.
  Interactive main-session background commands can outlive a final response,
  but stop when Claude exits. Noninteractive `-p` runs have a shorter lifetime.
- `Monitor` can deliver command-output lines or WebSocket messages to the
  conversation. Have the observer emit relevant changes, not every poll's
  unchanged status or every noisy log line.
- Channels can push external events into an open conversation. Availability
  varies by installed version, provider, and configuration; inspect actual
  tools before choosing one.

See [background commands and Monitor](https://code.claude.com/docs/en/tools-reference),
[Channels](https://code.claude.com/docs/en/channels), and
[scheduled task lifetime limits](https://code.claude.com/docs/en/scheduled-tasks#limitations).
Background Bash and Monitor tasks are not restored on resume; some scheduled
prompts are, but they still invoke the model on their schedule.

### Codex and other harnesses

Use the background execution and notification facilities actually advertised
by the current harness. A tool waiting for completion does not require model
inference while it waits; do not convert it into repeated short status checks.
A detached PID or output file alone does not arrange a future agent turn.

Codex's [App Server](https://learn.chatgpt.com/docs/app-server) provides APIs for
feeding external tool output into a thread, but an API primitive is not an
already-configured notification tool. Do not invent a CLI command or manually
wire an app-server bridge as a routine waiting workaround. In Omnigent, use
`watch-anything` when native notification is insufficient and the condition
fits that skill. Outside Omnigent, its watcher cannot target a native thread ID.

## Make the wait useful and bounded

- Check whether the condition is already satisfied and act immediately if so.
  Establish a change watch before starting work when possible, and recheck
  after registration if needed to close the check/subscribe race. A comparison
  that already prints `MATCH` is still a silent baseline for a change watcher.
- Distinguish a probe failure from an unchanged value. Bound background scripts
  and define what happens on failure or timeout, as well as on success.
- Avoid duplicate native/custom observers for the same condition. Record enough
  context to know what the eventual notification means and what to do next.
- After registering, continue other work or finish the turn. Omnigent defers
  delivery while a session is busy; holding it open can delay its own wake.
- Read current state when notified; notifications may be delayed, batched, or
  race further changes. Retries check prior acceptance and refresh obsolete
  feedback, but duplicate suppression is best effort.
  Cancel the observer when done or when the task is handed off.
- If no suitable mechanism is available, explain the missing capability and
  limits. Do not silently start repeated model polling or restart/install
  infrastructure without authorization.

For production health signals, prefer the service's alerting and automated
health checks. Wake an agent when interpretation or follow-up needs judgment;
use deterministic automation when it can complete the task itself.
