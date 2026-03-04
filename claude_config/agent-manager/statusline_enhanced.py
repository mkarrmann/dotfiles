#!/usr/bin/env python3
"""Enhanced statusline with context tracking and alerts.

Provides a compact statusline showing:
- Active agent status with granular states
- Context percentage with color coding
- Alert indicators (permission, errors, high context)
- Multi-agent summary
"""

import sys
from pathlib import Path

try:
    from agent_state_enhanced import load_agents, C
except ImportError:
    from agent_state import load_agents, C


def format_context(pct):
    """Format context percentage with color."""
    if pct is None:
        return ""
    if pct > 85:
        return f"{C.RED}{pct}%{C.RESET}"
    elif pct > 70:
        return f"{C.YELLOW}{pct}%{C.RESET}"
    else:
        return f"{pct}%"


def generate_statusline(compact=False):
    """Generate enhanced statusline."""
    try:
        agents = load_agents()
    except:
        return ""

    # Filter to active agents
    active = [a for a in agents if a.is_live]
    if not active:
        return ""

    # Detect alerts
    alerts = []
    permission_count = 0
    error_count = 0
    high_context_count = 0

    for a in active:
        if hasattr(a, 'is_permission_prompt') and a.is_permission_prompt:
            permission_count += 1
        if hasattr(a, 'is_error') and a.is_error:
            error_count += 1
        if hasattr(a, 'context_pct') and a.context_pct and a.context_pct > 85:
            high_context_count += 1

    # Build alert string
    alert_parts = []
    if permission_count > 0:
        alert_parts.append(f"{C.RED}🔐{C.RESET}")
    if error_count > 0:
        alert_parts.append(f"{C.RED}❌{C.RESET}")
    if high_context_count > 0:
        alert_parts.append(f"{C.YELLOW}⚠️{C.RESET}")

    alert_str = " ".join(alert_parts)

    # Get primary agent (first active or one with highest priority)
    priority_order = ['permission', 'error', 'tool', 'thinking', 'writing', 'reading', 'searching']
    primary = active[0]
    for status in priority_order:
        for a in active:
            if a.smart_status == status:
                primary = a
                break
        if primary.smart_status in priority_order:
            break

    # Get icon for primary agent
    status_icons = {
        'thinking': '💭',
        'tool': '🔧',
        'writing': '✏️',
        'reading': '📖',
        'searching': '🔍',
        'permission': '🔐',
        'waiting': '⚠️',
        'error': '❌',
        'active': '⚡',
    }
    icon = status_icons.get(primary.smart_status, '⚡')

    # Build statusline
    if compact:
        # Ultra-compact for tmux statusline
        if len(active) == 1:
            ctx_str = ""
            if hasattr(primary, 'context_pct') and primary.context_pct:
                ctx_str = f" {format_context(primary.context_pct)}"
            result = f"{icon} {primary.name}{ctx_str}"
        else:
            result = f"{icon} {primary.name} +{len(active) - 1}"

        if alert_str:
            result += f" {alert_str}"

        return result

    else:
        # Richer format for terminal display
        parts = [f"{icon} {primary.name}"]

        # Add context for primary
        if hasattr(primary, 'context_pct') and primary.context_pct:
            parts.append(format_context(primary.context_pct))

        # Add count if multiple
        if len(active) > 1:
            parts.append(f"+{len(active) - 1}")

        # Add alerts
        if alert_str:
            parts.append(alert_str)

        # Add detail if available
        if primary.smart_detail:
            detail = primary.smart_detail[:30]
            parts.append(f"{C.DIM}({detail}){C.RESET}")

        return " ".join(parts)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Enhanced statusline")
    parser.add_argument("--compact", action="store_true", help="Ultra-compact for tmux")
    parser.add_argument("--no-color", action="store_true", help="Disable colors")
    args = parser.parse_args()

    if args.no_color:
        # Disable all colors
        for attr in dir(C):
            if not attr.startswith('_'):
                setattr(C, attr, '')

    status = generate_statusline(compact=args.compact)
    if status:
        print(status)


if __name__ == "__main__":
    main()