#!/usr/bin/env python3
"""Enhanced status detection patterns synthesized from tmux-orchestrator.

This module provides sophisticated pattern matching for Claude Code status detection,
incorporating the best patterns from tmux-orchestrator while maintaining compatibility
with your existing agent-manager system.
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class StatusResult:
    """Enhanced status result with granular details."""
    state: str  # Primary state: thinking, tool, writing, reading, searching, waiting, complete, idle
    detail: str  # Specific details about what's happening
    context_pct: Optional[int] = None
    is_permission_prompt: bool = False
    is_error: bool = False
    indicator: Optional[str] = None  # Special indicators (build succeeded, tests passed, etc.)
    confidence: float = 1.0  # How confident we are in this detection


class EnhancedStatusDetector:
    """Sophisticated status detection using patterns from tmux-orchestrator."""

    def __init__(self):
        # Thinking/processing patterns from orchestrator
        self.THINKING_PATTERNS = {
            'active': r'[✻✶✽✢]\s+\S+(?:\s+\S+)*…\s*(?:\(|$)',  # Star + verb + ellipsis
            'completed': r'[✻✶✽✢]\s+\S+.*?\sfor\s+\d+[sm]',    # Star + verb + "for" duration
            'spinning': r'[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]',                    # Spinner characters
        }

        # Tool execution patterns
        self.TOOL_PATTERNS = {
            'indicator': r'◐\s*(\w+)',  # Universal tool running indicator
            'bash': r'(?:Running|Executing)[:\s]+(.+?)(?:\n|$)',
            'write': r'(?:Writ|Edit|Creat)\w*\s+(\S+)',
            'read': r'Read(?:ing)?\s+(\S+)',
            'search': r'(?:Grep|Glob|Search)\w*\s+(.*?)(?:\n|$)',
            'task': r'◐\s*Task',  # Task agent running
            'skill': r'◐\s*Skill',  # Skill execution
        }

        # Permission and prompt patterns
        self.PERMISSION_PATTERNS = {
            'bash': r'Do you want to proceed\?',
            'skill': r'Use skill "[^"]+"\?',
            'edit_waiting': r'⏵⏵ accept edits(?!\s+on)',  # Negative lookahead critical!
            'edit_active': r'⏵⏵ accept edits on',  # Auto-accept is ON (not a prompt)
            'plan': r'Would you like to proceed\?',
            'user_question': r'Enter to select.*Esc to cancel',
        }

        # Context extraction patterns (statusline formats)
        self.CONTEXT_PATTERNS = {
            'claude_hud': r'\[(Opus|Sonnet|Haiku)(?:\s+[\d.]+)?(?:\s+\([^)]*\))?\].*?[█░]+\s*(\d+)%',
            'statusline_pro': r'🧠\s*(\d+)%',
            'simple': r'(\d+)%\s*context',
        }

        # Error and success indicators
        self.INDICATORS = {
            'error': [
                (r'❌', 'error_emoji'),
                (r'FAILED', 'test_failure'),
                (r'⎿.*[Ee]rror:', 'tool_error'),
                (r'^[Ee]rror:', 'direct_error'),
                (r'Traceback \(most recent call last\)', 'python_traceback'),
            ],
            'success': [
                (r'✅', 'success_emoji'),
                (r'Build succeeded', 'build_success'),
                (r'PASSED', 'tests_passed'),
                (r'All tests passed', 'all_tests_passed'),
                (r'completed successfully', 'task_completed'),
            ],
            'warning': [
                (r'⚠️', 'warning_emoji'),
                (r'WARNING:', 'warning_message'),
            ],
        }

        # Enhanced chrome filtering for Neovim
        self.CHROME_PATTERNS = [
            r'TERMINAL\s+term:',        # Neovim terminal mode
            r'NORMAL\s+term:',          # Neovim normal mode
            r'INSERT\s+.*term:',        # Neovim insert mode
            r'-- INSERT --',            # Vim insert indicator
            r'-- NORMAL --',            # Vim normal indicator
            r'-- VISUAL --',            # Vim visual indicator
            r'⏵⏵\s*accept',            # Claude accept hint
            r'shift\+tab to cycle',     # Claude mode cycling
            r'📁\s+\w+.*🤖',           # Claude statusline
            r'Bot \d+:\d+',            # Neovim statusline
            r'^\s*\d+\s+\d+\s*$',      # Line number pairs
            r'▏',                       # Indent guides
            r'~$',                      # Vim empty line indicator
            r'^\[No Name\]',           # Vim buffer name
        ]

    def detect_comprehensive_status(self, pane_lines: List[str]) -> StatusResult:
        """Perform comprehensive status detection with all patterns."""
        if not pane_lines:
            return StatusResult('unknown', 'No pane content')

        # Join recent lines for analysis
        chunk = '\n'.join(pane_lines[-20:])

        # First check for permission prompts (highest priority)
        perm_result = self._check_permission_prompts(chunk)
        if perm_result:
            return perm_result

        # Check for active thinking
        think_result = self._check_thinking(chunk)
        if think_result and think_result.state == 'thinking':
            return think_result

        # Check for tool execution
        tool_result = self._check_tools(chunk)
        if tool_result:
            return tool_result

        # Check for specific activities (writing, reading, searching)
        activity_result = self._check_specific_activities(chunk)
        if activity_result:
            return activity_result

        # Check for completion
        if think_result and think_result.state == 'complete':
            return think_result

        # Check for errors/success
        indicator_result = self._check_indicators(chunk)
        if indicator_result:
            return indicator_result

        # Extract context percentage if available
        context_pct = self._extract_context(chunk)

        # Check if idle (has prompt)
        if self._has_claude_prompt(chunk):
            return StatusResult('idle', 'Waiting for input', context_pct=context_pct)

        # Default to active
        return StatusResult('active', 'Processing', context_pct=context_pct)

    def _check_permission_prompts(self, chunk: str) -> Optional[StatusResult]:
        """Check for permission prompts that need user action."""
        for perm_type, pattern in self.PERMISSION_PATTERNS.items():
            if perm_type == 'edit_active':
                continue  # Skip the "on" pattern - it's not a prompt
            if re.search(pattern, chunk):
                return StatusResult(
                    'waiting',
                    f'Permission required: {perm_type}',
                    is_permission_prompt=True
                )
        return None

    def _check_thinking(self, chunk: str) -> Optional[StatusResult]:
        """Check for thinking/processing states."""
        # Active thinking with ellipsis
        if re.search(self.THINKING_PATTERNS['active'], chunk):
            # Extract the verb for detail
            match = re.search(r'[✻✶✽✢]\s+(\S+(?:\s+\S+)*)…', chunk)
            if match:
                verb = match.group(1).strip()
                return StatusResult('thinking', f'{verb}...', confidence=0.95)
            return StatusResult('thinking', 'Processing...', confidence=0.9)

        # Completed thinking
        if re.search(self.THINKING_PATTERNS['completed'], chunk):
            match = re.search(r'[✻✶✽✢]\s+(\S+.*?)\sfor\s+(\d+[sm])', chunk)
            if match:
                verb = match.group(1).strip()
                duration = match.group(2)
                return StatusResult('complete', f'{verb} for {duration}')

        # Spinner detection
        if any(char in chunk for char in self.THINKING_PATTERNS['spinning']):
            return StatusResult('thinking', 'Processing', confidence=0.8)

        return None

    def _check_tools(self, chunk: str) -> Optional[StatusResult]:
        """Check for tool execution."""
        # Check for ◐ indicator first (most reliable)
        if '◐' in chunk:
            # Try to extract tool name
            match = re.search(self.TOOL_PATTERNS['indicator'], chunk)
            if match:
                tool_name = match.group(1)
                return StatusResult('tool', f'Running {tool_name}')

            # Check for specific tool patterns
            for tool_type in ['Task', 'Skill', 'Bash', 'Edit', 'Write', 'Read']:
                if f'◐ {tool_type}' in chunk:
                    return StatusResult('tool', f'Running {tool_type}')

            return StatusResult('tool', 'Tool executing')

        # Check bash execution patterns
        match = re.search(self.TOOL_PATTERNS['bash'], chunk)
        if match:
            command = match.group(1).strip()[:60]
            return StatusResult('tool', f'Executing: {command}')

        return None

    def _check_specific_activities(self, chunk: str) -> Optional[StatusResult]:
        """Check for specific activities like writing, reading, searching."""
        # Writing/Editing
        match = re.search(self.TOOL_PATTERNS['write'], chunk)
        if match:
            file = match.group(1)[:40]
            return StatusResult('writing', f'Editing {file}')

        # Reading
        match = re.search(self.TOOL_PATTERNS['read'], chunk)
        if match:
            file = match.group(1)[:40]
            return StatusResult('reading', f'Reading {file}')

        # Searching
        match = re.search(self.TOOL_PATTERNS['search'], chunk)
        if match:
            query = match.group(1).strip()[:40]
            return StatusResult('searching', f'Searching: {query}')

        return None

    def _check_indicators(self, chunk: str) -> Optional[StatusResult]:
        """Check for error/success indicators."""
        # Check recent lines only (last 5) for error indicators
        recent_lines = chunk.split('\n')[-5:]
        recent_chunk = '\n'.join(recent_lines)

        # Check errors
        for pattern, indicator_type in self.INDICATORS['error']:
            if re.search(pattern, recent_chunk, re.MULTILINE):
                return StatusResult(
                    'active',
                    indicator_type.replace('_', ' ').title(),
                    is_error=True,
                    indicator=indicator_type
                )

        # Check success
        for pattern, indicator_type in self.INDICATORS['success']:
            if re.search(pattern, recent_chunk):
                return StatusResult(
                    'complete',
                    indicator_type.replace('_', ' ').title(),
                    indicator=indicator_type
                )

        return None

    def _extract_context(self, chunk: str) -> Optional[int]:
        """Extract context percentage from statusline."""
        for pattern_name, pattern in self.CONTEXT_PATTERNS.items():
            matches = re.findall(pattern, chunk)
            if matches:
                if pattern_name == 'claude_hud':
                    # Format: [(model, percentage), ...]
                    return int(matches[-1][1]) if len(matches[-1]) > 1 else None
                else:
                    # Simple percentage
                    return int(matches[-1])
        return None

    def _has_claude_prompt(self, chunk: str) -> bool:
        """Check if Claude prompt is visible (indicates idle state)."""
        # Look for the > prompt at start of line
        return bool(re.search(r'^>\s*$', chunk, re.MULTILINE))

    def is_chrome_line(self, line: str) -> bool:
        """Check if a line is chrome (UI elements, not content)."""
        return any(re.search(pattern, line) for pattern in self.CHROME_PATTERNS)

    def filter_chrome(self, lines: List[str]) -> List[str]:
        """Filter out chrome lines from pane content."""
        return [line for line in lines if not self.is_chrome_line(line)]


# Convenience function for backward compatibility
def detect_enhanced_status(pane_lines: List[str]) -> Tuple[str, str, Optional[int]]:
    """Simple wrapper that returns (status, detail, context_pct) tuple."""
    detector = EnhancedStatusDetector()
    result = detector.detect_comprehensive_status(pane_lines)
    return result.state, result.detail, result.context_pct