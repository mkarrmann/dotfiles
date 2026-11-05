"""Omnigent approvals as OpenCode permission requests and forms, and T3 Code's rules over them.

An Omnigent `response.elicitation_request` comes from one of four places, told
apart by `policy_name` (Omnigent has no kind field):

- this server's own session policy (`T3_POLICY`), which asks before every
  file and shell tool call while the thread is not in full access;
- the harness's own permission prompt: claude-native's PermissionRequest hook
  (`claude_native_permission`) or a codex-native approval (`codex_native_*`);
- any other Omnigent policy that answered ASK: the operator's guardrails, such
  as a gate on new environments;
- a structured question (an `ask_user_question` extra), which is a form.

T3 Code enforces nothing itself (`runtimePolicy.enforcement: "native"`): it
sends permission rules, last match wins, and expects the server to apply them.
The rules decide the first two kinds. The operator's policy asks always reach
the user: they are guardrails set outside T3 Code that a full-access thread
must not switch off.

The harnesses run in the operator's permission mode (bypassPermissions here),
so they rarely ask on their own; the session policy is what makes them ask. It
is enforced by Omnigent in the harnesses' PreToolUse hooks, whatever their
mode, and switched over REST, where a harness mode is switched by driving its
terminal (unreliable while the terminal starts, and Claude cannot return to
bypassPermissions).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .wire import Json, JsonObject

Effect = Literal["allow", "deny", "ask"]
Source = Literal["t3", "claude", "codex", "policy"]
RuntimeMode = Literal["full-access", "auto-accept-edits", "approval-required"]

T3_POLICY = "t3-code-runtime-mode"
T3_POLICY_HANDLER = "omnigent.policies.builtins.safety.ask_on_os_tools"

CLAUDE_SHELL = {"Bash"}
CLAUDE_EDIT = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CLAUDE_READ = {"Read", "Glob", "Grep", "LS", "NotebookRead"}
CLAUDE_FETCH = {"WebFetch", "WebSearch"}


@dataclass(frozen=True)
class Question:
    key: str
    """The form field key; the answer goes back keyed by `answer_key`."""
    answer_key: str
    header: str | None
    question: str
    options: list[tuple[str, str | None]]
    multi: bool
    custom: bool


@dataclass(frozen=True)
class Ask:
    """What one elicitation asks, in OpenCode's terms."""

    elicitation_id: str
    target_session_id: str | None
    source: Source
    action: str
    resources: list[str]
    message: str
    save: list[str] = field(default_factory=list)
    tool: str | None = None
    call_id: str | None = None
    questions: list[Question] = field(default_factory=list)
    plan_exit: bool = False
    paths: list[str] = field(default_factory=list)
    """Files the call touches, for the `external_directory` rules."""

    @property
    def governed(self) -> bool:
        """Whether T3 Code's rules decide it; the operator's policies always ask the user."""
        return self.source != "policy"


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _claude_input(preview: Any, tool: str) -> dict[str, Any]:
    """The tool input of a Claude prompt; its preview is `Tool({json})`, the JSON cut at 1 KiB."""
    if not isinstance(preview, str) or not preview.startswith(f"{tool}(") or not preview.endswith(")"):
        return {}
    parsed = _json_or_none(preview[len(tool) + 1 : -1])
    return parsed if isinstance(parsed, dict) else {}


def _policy_call(preview: Any) -> tuple[str | None, dict[str, Any]]:
    parsed = _json_or_none(preview) if isinstance(preview, str) else None
    if not isinstance(parsed, dict):
        return None, {}
    arguments = parsed.get("arguments")
    if isinstance(arguments, str):
        arguments = _json_or_none(arguments)
    return (str(parsed["name"]) if parsed.get("name") else None), (arguments if isinstance(arguments, dict) else {})


def _command_pattern(command: str) -> list[str]:
    """OpenCode's grant for a command: its program, any arguments (`rm *` also matches bare `rm`)."""
    words = command.split()
    return [f"{words[0]} *"] if words else []


def _questions(extra: Any) -> list[Question]:
    if not isinstance(extra, dict) or not isinstance(extra.get("questions"), list):
        return []
    questions: list[Question] = []
    for n, entry in enumerate(extra["questions"]):
        if not isinstance(entry, dict) or not isinstance(entry.get("question"), str):
            continue
        options = [
            (str(o["label"]), o.get("description") if isinstance(o.get("description"), str) else None)
            for o in entry.get("options") or []
            if isinstance(o, dict) and isinstance(o.get("label"), str)
        ]
        native_id = entry.get("id") if isinstance(entry.get("id"), str) and entry.get("id") else None
        questions.append(
            Question(
                key=native_id or f"q{n + 1}",
                # Codex keys answers by question id, Claude (whose questions have none) by the question text.
                answer_key=native_id or entry["question"],
                header=entry.get("header") if isinstance(entry.get("header"), str) else None,
                question=entry["question"],
                options=options,
                multi=entry.get("multiSelect") is True,
                custom=entry.get("isOther") is not False and not entry.get("isSecret"),
            )
        )
    return questions


def _tool_call(tool: str, tool_input: dict[str, Any], fallback: str) -> tuple[str, list[str], dict[str, Any]]:
    """The action, resources and other `Ask` fields of a Claude-named tool call; codex-native's hooks use the same names."""
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") or "")
    paths = [path] if path else []
    if tool in CLAUDE_SHELL:
        command = str(tool_input.get("command") or "")
        return "shell", [command or fallback], {"tool": tool, "save": _command_pattern(command)}
    if tool in CLAUDE_EDIT:
        return "edit", [path], {"tool": tool, "save": ["*"], "paths": paths}
    if tool in CLAUDE_READ:
        return "read", [path or str(tool_input.get("pattern") or "")], {"tool": tool, "save": ["*"], "paths": paths}
    if tool in CLAUDE_FETCH:
        return "webfetch", [str(tool_input.get("url") or tool_input.get("query") or "")], {"tool": tool, "save": ["*"]}
    return tool or "tool", [fallback], {"tool": tool, "save": ["*"]}


def classify(data: dict[str, Any]) -> Ask:
    params: dict[str, Any] = data.get("params") or {}
    elicitation_id = str(data.get("elicitation_id"))
    target = params.get("target_session_id") if isinstance(params.get("target_session_id"), str) else None
    message = str(params.get("message") or "Omnigent requests approval")
    policy = str(params.get("policy_name") or "")
    preview = params.get("content_preview")
    questions = _questions(params.get("ask_user_question"))

    def ask(source: Source, action: str, resources: list[str], **extra: Any) -> Ask:
        return Ask(elicitation_id, target, source, action, [r for r in resources if r] or [message], message, **extra)

    if policy == "claude_native_permission":
        tool = str(params.get("tool_name") or "")
        tool_input = _claude_input(preview, tool)
        if questions:
            return ask("claude", "question", [], tool=tool, questions=questions)
        if params.get("exit_plan_mode") is not None or tool == "ExitPlanMode":
            plan = (params.get("exit_plan_mode") or {}).get("plan") if isinstance(params.get("exit_plan_mode"), dict) else None
            return ask("claude", "plan_exit", [str(plan or "Leave plan mode and start implementing")], tool=tool, plan_exit=True)
        directories = [d for d in params.get("permission_directories") or [] if isinstance(d, str)]
        if directories:
            return ask("claude", "external_directory", directories, tool=tool, save=[f"{d.rstrip('/')}/*" for d in directories])
        action, resources, extra = _tool_call(tool, tool_input, str(preview or ""))
        return ask("claude", action, resources, **extra)

    if policy == T3_POLICY:
        name, arguments = _policy_call(preview)
        action, resources, extra = _tool_call(name or "tool", arguments, str(preview or ""))
        return ask("t3", action, resources, **extra)

    if policy.startswith("codex_native_") or params.get("codex_method"):
        call_id = params.get("call_id") if isinstance(params.get("call_id"), str) else None
        if questions:
            return ask("codex", "question", [], questions=questions)
        if policy == "codex_native_command_approval":
            command = str(params.get("command") or "")
            return ask("codex", "shell", [command], call_id=call_id, save=_command_pattern(command))
        phase = str(params.get("phase") or "")
        if phase in ("codex_file_change_approval", "codex_apply_patch_approval"):
            files = [str(f) for f in params.get("files") or [] if isinstance(f, str)]
            root = params.get("grant_root")
            return ask("codex", "edit", files or ([str(root)] if root else []), call_id=call_id, save=["*"])
        if phase == "codex_permissions_approval":
            # A sandbox escalation (network, writes outside the workspace) Codex grants for one turn only.
            return ask("codex", "permissions", [str(params.get("reason") or message)])
        return ask("codex", phase.removeprefix("codex_") or "codex", [message])

    if questions:
        return ask("policy", "question", [], questions=questions)
    name, arguments = _policy_call(preview)
    called = arguments.get("command")
    resource = called if isinstance(called, str) else (f"{name}({json.dumps(arguments)})" if name else str(preview or ""))
    return ask("policy", policy or "policy", [resource, message] if resource != message else [message], tool=name)


# -- rules --------------------------------------------------------------------


def _pattern(glob: str) -> re.Pattern[str]:
    """OpenCode's wildcard: `*` any run, `?` one character, and a trailing ` *` may match nothing."""
    body = glob[:-2] if glob.endswith(" *") else glob
    regex = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in body)
    return re.compile(f"{regex}( .*)?" if glob.endswith(" *") else regex, re.DOTALL)


def matches(glob: str, value: str) -> bool:
    return _pattern(glob).fullmatch(value) is not None


def _rules(rules: list[Json]) -> list[tuple[str, str, Effect]]:
    parsed: list[tuple[str, str, Effect]] = []
    for rule in rules:
        if isinstance(rule, dict) and rule.get("effect") in ("allow", "deny", "ask"):
            parsed.append((str(rule.get("action")), str(rule.get("resource")), rule["effect"]))  # type: ignore[arg-type]
    return parsed


def evaluate(rules: list[Json], action: str, resources: list[str]) -> Effect:
    """The verdict of `rules` (last match wins) on every resource: one deny denies, else one ask asks.

    A resource no rule matches is allowed, as OpenCode's default agent rules
    (`"*": allow`) under T3 Code's would allow it: T3 Code only writes rules
    for what it wants asked or denied.
    """
    parsed = _rules(rules)
    verdicts: list[Effect] = []
    for resource in resources:
        verdict: Effect = "allow"
        for rule_action, rule_resource, effect in parsed:
            if matches(rule_action, action) and matches(rule_resource, resource):
                verdict = effect
        verdicts.append(verdict)
    if "deny" in verdicts:
        return "deny"
    return "ask" if "ask" in verdicts else "allow"


def runtime_mode(rules: list[Json]) -> RuntimeMode | None:
    """The T3 Code runtime mode the rules were built from; `None` before T3 Code sent any."""
    parsed = _rules(rules)
    if not parsed:
        return None
    if ("*", "*", "allow") in parsed:
        return "full-access"
    if ("edit", "*", "allow") in parsed:
        return "auto-accept-edits"
    return "approval-required"


def wants_policy(rules: list[Json], plan: bool) -> bool:
    """Whether the thread needs the harness to ask before file and shell tools: not in full access, or planning."""
    mode = runtime_mode(rules)
    return plan or (mode is not None and mode != "full-access")


# -- resolutions ------------------------------------------------------------------


Answer = Literal["allow", "deny", "stop"]
"""T3 Code's answers: approve; Decline, after which the agent goes on without the call; Cancel, which stops the turn."""


@dataclass(frozen=True)
class Resolution:
    body: JsonObject
    """Omnigent's elicitation resolve body."""
    ends_turn: bool
    """Whether Omnigent stops the turn on this answer, so the execution must end."""


def resolution(ask: Ask, answer: Answer, feedback: str | None = None) -> Resolution:
    """Omnigent's verbs for T3 Code's answer, which differ by who asked.

    - Claude's own prompt: `decline` (and `cancel`) deny the call, and the
      turn goes on; `feedback` reaches the model as the reason.
    - A policy ask: `cancel` denies the call and the turn goes on; `decline`
      aborts the turn (`_hold_native_ask_gate_impl`).
    - Codex's own prompt: Omnigent interrupts the turn on `decline`, and
      Codex's `cancel` aborts it; nothing lets it go on.
    """
    if answer == "allow":
        return Resolution({"action": "accept"}, False)
    if ask.source == "claude":
        body: JsonObject = {"action": "decline"}
        if answer == "deny" and feedback:
            body["content"] = {"feedback": feedback}
        return Resolution(body, answer == "stop")
    if ask.source == "codex":
        return Resolution({"action": "decline" if answer == "deny" else "cancel"}, True)
    return Resolution({"action": "cancel" if answer == "deny" else "decline"}, answer == "stop")


def form(ask: Ask, form_id: str, session_id: str) -> JsonObject:
    fields: list[Json] = []
    for question in ask.questions:
        entry: JsonObject = {
            "key": question.key,
            "type": "multiselect" if question.multi else "string",
            "title": question.header or question.question,
            "description": question.question,
            "options": [
                {"value": label, "label": label, **({"description": description} if description else {})}
                for label, description in question.options
            ],
        }
        if question.custom:
            entry["custom"] = True
        fields.append(entry)
    return {"id": form_id, "sessionID": session_id, "title": "Question", "fields": fields}


def form_answer(ask: Ask, answer: dict[str, Any]) -> JsonObject:
    """T3 Code's answer, keyed as the harness reads it."""
    content: JsonObject = {}
    for question in ask.questions:
        value = answer.get(question.key)
        if isinstance(value, list):
            values: list[Json] = [str(v) for v in value]
            content[question.answer_key] = values if question.multi else ", ".join(str(v) for v in values)
        elif value is not None:
            content[question.answer_key] = [str(value)] if question.multi else str(value)
    return content
