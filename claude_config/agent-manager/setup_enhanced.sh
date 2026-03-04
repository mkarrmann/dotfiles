#!/bin/bash
# Integration script for enhanced agent manager
# Run this to set up the enhanced features

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Enhanced Agent Manager Setup"
echo "============================"
echo ""

# Check if enhanced modules are available
echo "Checking enhanced modules..."
if python3 -c "import sys; sys.path.insert(0, '$SCRIPT_DIR'); from agent_state_enhanced import load_agents" 2>/dev/null; then
    echo "✅ Enhanced agent_state module loaded"
else
    echo "⚠️  Enhanced agent_state not available, using original"
fi

if python3 -c "import sys; sys.path.insert(0, '$SCRIPT_DIR'); from enhanced_status_patterns import EnhancedStatusDetector" 2>/dev/null; then
    echo "✅ Enhanced status patterns loaded"
else
    echo "⚠️  Enhanced patterns not available"
fi

echo ""
echo "Available Commands:"
echo "==================="
echo ""

echo "1. Enhanced Dashboard (interactive TUI):"
echo "   python3 $SCRIPT_DIR/dashboard_enhanced.py"
echo ""

echo "2. Enhanced Status Summary:"
echo "   python3 $SCRIPT_DIR/dashboard_enhanced.py --summary"
echo ""

echo "3. Enhanced Statusline (for tmux/terminal):"
echo "   python3 $SCRIPT_DIR/statusline_enhanced.py"
echo "   python3 $SCRIPT_DIR/statusline_enhanced.py --compact  # For tmux"
echo ""

echo "4. Test Enhanced Detection on Current Agents:"
echo "   python3 -c \"
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from agent_state_enhanced import load_agents, print_summary
agents = load_agents()
print_summary(agents, show_all=True)
\""
echo ""

echo "Optional: Add to your tmux status line:"
echo "========================================="
echo "Add to ~/.tmux.conf:"
echo '  set -g status-right "#(python3 '$SCRIPT_DIR'/statusline_enhanced.py --compact) | %H:%M"'
echo ""

echo "Optional: Create aliases in ~/.bashrc:"
echo "======================================"
echo "alias agentdash='python3 $SCRIPT_DIR/dashboard_enhanced.py'"
echo "alias agentstatus='python3 $SCRIPT_DIR/statusline_enhanced.py'"
echo ""

echo "Testing Enhanced Detection:"
echo "==========================="
echo ""

# Run a quick test
python3 <<EOF
import sys
sys.path.insert(0, '$SCRIPT_DIR')

try:
    from agent_state_enhanced import load_agents
    from enhanced_status_patterns import EnhancedStatusDetector

    print("Loading current agents...")
    agents = load_agents()

    if agents:
        print(f"Found {len(agents)} agents\n")

        # Show enhanced detection for first active agent
        for a in agents:
            if a.is_live and a.pane_lines:
                print(f"Agent: {a.name}")
                print(f"  Original status: {a.status_text}")
                print(f"  Enhanced status: {a.smart_status}")
                print(f"  Detail: {a.smart_detail}")

                if hasattr(a, 'context_pct') and a.context_pct:
                    print(f"  Context: {a.context_pct}%")

                if hasattr(a, 'is_permission_prompt') and a.is_permission_prompt:
                    print("  ⚠️  Permission prompt detected!")

                if hasattr(a, 'is_error') and a.is_error:
                    print("  ❌ Error detected!")

                print("")
                break
    else:
        print("No agents found. Start some Claude sessions to test.")

except Exception as e:
    print(f"Error: {e}")
EOF

echo ""
echo "Setup complete! Try running the enhanced dashboard:"
echo "  python3 $SCRIPT_DIR/dashboard_enhanced.py"