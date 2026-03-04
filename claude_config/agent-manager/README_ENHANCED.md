# Enhanced Agent Manager

This enhanced version of your agent manager incorporates the best status detection patterns from tmux-orchestrator while maintaining full Neovim compatibility.

## Key Enhancements

### 1. **Granular Status Detection**
- **Thinking states**: Detects "✶ Vibing…", "✻ Skedaddling…" and other thinking indicators
- **Tool execution**: Recognizes `◐` indicator and specific tools (Bash, Task, Skill, etc.)
- **Activity detection**: Writing, reading, searching with file/query details
- **Permission prompts**: Detects when Claude needs approval
- **Error states**: Identifies errors, failures, and tracebacks

### 2. **Context Percentage Tracking**
- Extracts context % from claude-hud-meta and meta-statusline-pro
- Visual indicators: 🟢 Green (<70%), 🟡 Yellow (70-85%), 🔴 Red (>85%)
- Alerts when context is critically high

### 3. **Alert System**
- 🔐 Permission required
- ❌ Error detected
- ⚠️ High context usage
- 💤 Long idle times

### 4. **Neovim-Aware Chrome Filtering**
Enhanced filtering for Neovim terminal modes:
- TERMINAL, NORMAL, INSERT, VISUAL mode indicators
- Line numbers, indent guides, status lines
- Vim empty line indicators (~)

## Files

### Core Modules
- `enhanced_status_patterns.py` - Pattern library from tmux-orchestrator
- `agent_state_enhanced.py` - Enhanced agent state with new detection
- `dashboard_enhanced.py` - Interactive TUI with alerts and context tracking
- `statusline_enhanced.py` - Rich statusline for tmux/terminal

### Setup
- `setup_enhanced.sh` - Test and configure enhanced features

## Usage

### Interactive Dashboard
```bash
python3 dashboard_enhanced.py
```

Features:
- Real-time status updates
- Context percentage bars
- Alert notifications
- Preview of agent output
- Keyboard navigation

Keys:
- `↑↓` - Navigate agents
- `Enter` - Focus agent's tmux window
- `s` - Toggle stopped agents
- `p` - Toggle preview
- `c` - Toggle context display
- `!` - Show alert details
- `r` - Refresh
- `q` - Quit

### Status Summary
```bash
python3 dashboard_enhanced.py --summary
```

### Statusline (for tmux)
```bash
# Compact for tmux status bar
python3 statusline_enhanced.py --compact

# Rich format for terminal
python3 statusline_enhanced.py
```

### Add to tmux.conf
```bash
set -g status-right "#(python3 ~/dotfiles/claude_config/agent-manager/statusline_enhanced.py --compact) | %H:%M"
```

### Shell Aliases
Add to ~/.bashrc:
```bash
alias agentdash='python3 ~/dotfiles/claude_config/agent-manager/dashboard_enhanced.py'
alias agentstatus='python3 ~/dotfiles/claude_config/agent-manager/statusline_enhanced.py'
```

## Pattern Examples

### Thinking Detection
```python
# Active: ✶ Vibing… (2m 24s · thinking)
# Completed: ✻ Cogitated for 52s
```

### Tool Execution
```python
# ◐ Bash - Running command
# ◐ Task - Task agent executing
# ◐ Edit - Editing file
```

### Context Extraction
```python
# [Opus 4.5] █████░░░░░ 45% | @80828
# devvm80828 | ... | 🧠 45% | ...
```

### Permission Prompts
```python
# Do you want to proceed?
# Use skill "skill-name"?
# ⏵⏵ accept edits (without "on" = waiting)
```

## Compatibility

- **Neovim**: Full compatibility with terminal mode detection
- **Original agent_state.py**: Falls back gracefully if enhanced not available
- **Existing workflows**: All your existing commands still work
- **Google Drive sync**: Continues to work with AGENTS.md on gdrive

## Comparison with Tmux Orchestrator

| Feature | Your Original | Tmux Orchestrator | This Enhanced Version |
|---------|--------------|-------------------|---------------------|
| Status Detection | Basic (active/idle) | Sophisticated patterns | Sophisticated patterns |
| Context Tracking | No | Yes (statusline) | Yes (extracted) |
| Permission Detection | No | Yes | Yes |
| Error Detection | Basic | Advanced | Advanced |
| Neovim Compatibility | Yes | No (send-keys issues) | Yes (full) |
| Cross-machine | Yes (gdrive) | No | Yes (gdrive) |
| LLM Classification | Yes (expensive) | No | Hybrid (fallback) |
| Real-time Updates | 30s poll | Sub-second | 2s poll |

## Benefits

1. **Better Awareness**: Know exactly what each agent is doing
2. **Context Management**: See when agents approach limits
3. **Alert System**: Never miss permission prompts or errors
4. **Reduced LLM Costs**: Pattern matching reduces need for Haiku classification
5. **Neovim Safe**: No send-keys, works perfectly with your workflow

## Technical Details

The enhanced detection uses a priority system:
1. Permission prompts (highest priority)
2. Active thinking
3. Tool execution
4. Specific activities (write/read/search)
5. Completion states
6. Error/success indicators
7. Idle detection (lowest priority)

Context percentage is extracted from:
- claude-hud-meta: `[Model] ████░░ 45%`
- meta-statusline-pro: `🧠 45%`
- Simple format: `45% context`

Chrome filtering removes:
- Neovim mode indicators
- Line numbers and indent guides
- Status lines and borders
- Empty line indicators

## Future Improvements

Potential additions:
- Historical context tracking (graph over time)
- Multi-machine coordination improvements
- Webhook alerts for critical states
- Integration with your task management
- Automatic context clearing suggestions