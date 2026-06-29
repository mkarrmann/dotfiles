# Stdin Schemas

Hooks receive JSON via stdin with context about the event.

## SessionStart

```json
{
  "session_id": "uuid",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "hook_event_name": "SessionStart",
  "source": "startup"
}
```

`source` values: `startup`, `resume`, `clear`, `compact`

## UserPromptSubmit

```json
{
  "session_id": "uuid",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "permission_mode": "acceptEdits",
  "hook_event_name": "UserPromptSubmit",
  "prompt": "The user's prompt text"
}
```

## PreToolUse

```json
{
  "session_id": "uuid",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "permission_mode": "acceptEdits",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {"command": "ls", "description": "List files"},
  "tool_use_id": "toolu_xxx"
}
```

## PostToolUse

```json
{
  "session_id": "uuid",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "permission_mode": "acceptEdits",
  "hook_event_name": "PostToolUse",
  "tool_name": "Bash",
  "tool_input": {"command": "ls", "description": "List files"},
  "tool_use_id": "toolu_xxx",
  "tool_result": "file1.txt\nfile2.txt"
}
```

## Stop

```json
{
  "session_id": "uuid",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "hook_event_name": "Stop",
  "stop_reason": "end_turn"
}
```

## Capturing Stdin for Debugging

```json
{
  "type": "command",
  "command": "cat > /tmp/hook-stdin.json"
}
```

Then inspect with: `cat /tmp/hook-stdin.json | python3 -m json.tool`
