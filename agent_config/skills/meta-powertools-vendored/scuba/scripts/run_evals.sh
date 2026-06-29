#!/bin/bash
# Run MSL Judge evals for the Scuba skill and observe with Skillwatch.
# Skillwatch Scuba Dashboard: https://skillwatch.nest.x2p.facebook.net/dashboard?project=scuba-evals
#
# NOTE: Local runs populate the SkillWatch dashboard by default, unless you
# explicitly pass --no-db. The --project flag tells SkillWatch where to file
# the results (this script defaults to "scuba-evals").
#
# Usage:
#   ./run_evals.sh                       # Run all evals (default)
#   ./run_evals.sh run-all               # Run all evals
#   ./run_evals.sh run-suite <suite>     # Run a specific suite (e.g., cli-queries, critical-rules)
#   ./run_evals.sh run <test-id>         # Run a single test (e.g., cli-join-001)
#   ./run_evals.sh list                  # List all available tests
#
# A/B Testing:
#   ./run_evals.sh run-ab --ab-test-b-side-commit <hash>              # A/B test all evals: current vs commit
#   ./run_evals.sh run-ab <suite> --ab-test-b-side-commit <hash>      # A/B test a suite vs commit
#   ./run_evals.sh run-ab <suite> --ab-test-b-side-no-skill           # A/B test: skill vs no-skill
#   ./run_evals.sh run-ab --ab-test-b-side-commit <hash> --infra-dir <path>  # Also A/B test infra code
#
# Auto-Discovery:
#   ./run_evals.sh run-auto <path>                    # Auto-discover and run evals from a path
#   ./run_evals.sh run-auto <path> -s "cli-.*"        # Filter suites by regex
#   ./run_evals.sh run-auto <path> -t "join"          # Filter test IDs by regex
#
# Utility Commands:
#   ./run_evals.sh info                               # Show SkillWatch framework info
#   ./run_evals.sh clean-worktrees                    # Clean up pooled reusable worktrees
#
# Project Management:
#   ./run_evals.sh create-project --name X --oncall-alias Y   # Create a SkillWatch project
#   ./run_evals.sh check-project --name X                     # Check if a project exists
#   ./run_evals.sh delete-project --name X                    # Delete a project
#
# Options (pass after the command):
#   --judge-rounds N    Number of judge rounds for majority voting (default: 3)
#   --workers N         Parallel workers (default: 4)
#   --verbose           Show detailed output
#   --show-judge-details Show agent response and judge feedback
#   --no-db             Disable database logging (prevents populating the dashboard)
#   --project NAME      SkillWatch project name for filing results (default: scuba-evals)
#   --output FILE       Save results to JSON file
#   --environment ENV   Environment: prod or test (default: test)
#   --no-analyze        Skip post-eval failure analysis and recommendations
#
# Additional options (passed through to the underlying CLI):
#   --num-runs N          Run each test N times for statistical significance
#   --agent-model MODEL   Model for agent execution
#   --judge-model MODEL   Model for judge evaluation
#   --metadata KEY=VALUE  Metadata pairs (repeatable)
#   --shard SHARD         XDB shard: prod, dev, or full name
#   --idle-timeout N      Kill agent if no stdout for N seconds
#   --view plain|rich     Output display mode
#   --plugin-dir PATH     Plugin directory (repeatable)
#   --judge-client TYPE   Judge client type (claude-cli/direct-api/bedrock)
#
# Examples:
#   ./run_evals.sh run-all --judge-rounds 1 --workers 2
#   ./run_evals.sh run-suite critical-rules --verbose --show-judge-details
#   ./run_evals.sh run cli-join-001 --verbose
#   ./run_evals.sh run-all --no-db                       # Run without populating the dashboard
#   ./run_evals.sh list
#   ./run_evals.sh run-ab --ab-test-b-side-commit abc123 --num-runs 3
#   ./run_evals.sh run-ab cli-queries --ab-test-b-side-no-skill
#   ./run_evals.sh run-auto ./eval_suites -s "cli-.*"
#
# Running evals directly with buck2 (without this wrapper):
#   buck2 run fbcode//msl/judge:run_eval -- run-all \
#     --eval-suites-dir ./eval_suites \
#     --project my-project-name

set -euo pipefail

# Resolve paths relative to this script's location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EVAL_SUITES_DIR="$SKILL_DIR/eval_suites"

# Defaults
JUDGE_ROUNDS=3
WORKERS=4
ANALYZE=true
COMMAND="${1:-run-all}"
EXTRA_ARGS=()

# Shift off the command if it's a recognized subcommand
case "$COMMAND" in
    run-all|run-suite|run|run-ab|run-auto|list|clean-worktrees|info|create-project|check-project|delete-project)
        shift || true
        ;;
    --*)
        # No subcommand given, default to run-all; don't shift
        COMMAND="run-all"
        ;;
esac

# For 'run' and 'run-suite', capture the required argument (test-id or suite name)
# For 'run-ab', target is optional (omit to run all evals)
# For 'run-auto', path is required
TARGET_ARG=""
case "$COMMAND" in
    run|run-suite)
        if [[ $# -gt 0 && ! "$1" =~ ^-- ]]; then
            TARGET_ARG="$1"
            shift
        else
            echo "Error: '$COMMAND' requires an argument (test ID or suite name)."
            echo "Available suites: cli-queries, core-queries, critical-rules, derived-columns, end-to-end, live, method-selection, time-and-comparison, udf-workflow"
            exit 1
        fi
        ;;
    run-ab)
        # Target is optional for run-ab (omit to run all evals)
        if [[ $# -gt 0 && ! "$1" =~ ^-- ]]; then
            TARGET_ARG="$1"
            shift
        fi
        ;;
    run-auto)
        if [[ $# -gt 0 && ! "$1" =~ ^-- ]]; then
            TARGET_ARG="$1"
            shift
        else
            echo "Error: 'run-auto' requires a path argument."
            echo "Provide a component dir, eval suite dir, or individual YAML file."
            exit 1
        fi
        ;;
esac

# Parse remaining options
while [[ $# -gt 0 ]]; do
    case "$1" in
        --judge-rounds)
            JUDGE_ROUNDS="$2"
            shift 2
            ;;
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --no-analyze)
            ANALYZE=false
            shift
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

# Validate run-ab has a required mode flag
if [[ "$COMMAND" == "run-ab" ]]; then
    EXTRA_STR="${EXTRA_ARGS[*]:-}"
    if [[ "$EXTRA_STR" != *"--ab-test-b-side-commit"* && "$EXTRA_STR" != *"--ab-test-b-side-no-skill"* ]]; then
        echo "Error: 'run-ab' requires either --ab-test-b-side-commit <hash> or --ab-test-b-side-no-skill"
        echo ""
        echo "Examples:"
        echo "  ./run_evals.sh run-ab --ab-test-b-side-commit abc123def"
        echo "  ./run_evals.sh run-ab cli-queries --ab-test-b-side-no-skill"
        echo "  ./run_evals.sh run-ab --ab-test-b-side-commit abc123 --num-runs 3"
        exit 1
    fi
fi

# Handle project management commands (uses a different binary)
case "$COMMAND" in
    create-project|check-project|delete-project)
        PROJECT_CMD="${COMMAND%-project}"  # extract: create, check, or delete
        echo "============================================"
        echo "  Scuba Skill - Project Management"
        echo "============================================"
        echo "Action:         $PROJECT_CMD"
        if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
            echo "Args:           ${EXTRA_ARGS[*]}"
        fi
        echo "============================================"
        echo ""
        CMD=(buck2 run fbcode//msl/judge:projects -- "$PROJECT_CMD")
        if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
            CMD+=("${EXTRA_ARGS[@]}")
        fi
        echo "Running: ${CMD[*]}"
        echo ""
        exec "${CMD[@]}"
        ;;
esac

echo "============================================"
echo "  Scuba Skill Evals (SkillWatch)"
echo "============================================"
echo "Command:        $COMMAND${TARGET_ARG:+ $TARGET_ARG}"
if [[ "$COMMAND" == "run-ab" ]]; then
    echo "Mode:           A/B Test"
fi
echo "Skill dir:      $SKILL_DIR"
echo "Eval suites:    $EVAL_SUITES_DIR"
echo "Judge rounds:   $JUDGE_ROUNDS"
echo "Workers:        $WORKERS"
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    echo "Extra args:     ${EXTRA_ARGS[*]}"
fi
echo "============================================"
echo ""

# Handle utility commands that don't need standard eval options
case "$COMMAND" in
    clean-worktrees|info)
        CMD=(buck2 run fbcode//msl/judge:run_eval -- "$COMMAND")
        if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
            CMD+=("${EXTRA_ARGS[@]}")
        fi
        echo "Running: ${CMD[*]}"
        echo ""
        exec "${CMD[@]}"
        ;;
esac

# Build the buck run command
CMD=(
    buck2 run fbcode//msl/judge:run_eval --
    "$COMMAND"
)

# Add target argument for run/run-suite/run-ab/run-auto
if [[ -n "$TARGET_ARG" ]]; then
    CMD+=("$TARGET_ARG")
fi

# Add standard options
CMD+=(
    --eval-suites-dir "$EVAL_SUITES_DIR"
    --skill-dir "$SKILL_DIR"
    --judge-rounds "$JUDGE_ROUNDS"
    --workers "$WORKERS"
    --show-judge-details
)

# Add any extra passthrough arguments
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    CMD+=("${EXTRA_ARGS[@]}")
fi

echo "Running: ${CMD[*]}"
echo ""

# Capture output for post-eval analysis
EVAL_OUTPUT_FILE=$(mktemp /tmp/scuba-eval-output.XXXXXX)
trap 'rm -f "$EVAL_OUTPUT_FILE"' EXIT

"${CMD[@]}" 2>&1 | tee "$EVAL_OUTPUT_FILE"
EVAL_EXIT_CODE=${PIPESTATUS[0]}

# Post-eval failure analysis
if [[ "$ANALYZE" == true && "$COMMAND" != "list" && $EVAL_EXIT_CODE -eq 0 ]]; then
    # Check if there were any failures or imperfect scores
    if grep -qE '(FAIL|score.*0\.[0-9]|Failed: [1-9])' "$EVAL_OUTPUT_FILE"; then
        echo ""
        echo "============================================"
        echo "  Analyzing failing signals..."
        echo "============================================"
        echo ""

        ANALYSIS_PROMPT="You are analyzing Scuba skill eval results to recommend SKILL.md improvements.

## Eval Output
$(cat "$EVAL_OUTPUT_FILE")

## Instructions
1. Identify every test that FAILED or scored below 1.0
2. For each, examine the judge feedback and agent response to determine the root cause
3. Categorize each issue as one of:
   - SKILL_FIX: The SKILL.md instructions are missing, unclear, or contradictory — recommend specific updates
   - EVAL_FIX: The eval expectation is unreasonable or conflicts with intended skill behavior — recommend eval yaml changes
   - AGENT_BEHAVIOR: The agent didn't follow existing instructions — no skill change needed
4. For SKILL_FIX and EVAL_FIX, provide the specific file, section, and recommended change

## Output Format
For each failing signal:
### <test-id> (score: X.XX)
**Root cause**: <brief description>
**Category**: SKILL_FIX | EVAL_FIX | AGENT_BEHAVIOR
**Recommendation**: <specific change with file path and content>

Then provide a summary of all recommended changes."

        CLAUDECODE= claude --print -p "$ANALYSIS_PROMPT"
    else
        echo ""
        echo "All tests passed with perfect scores — no analysis needed."
    fi
fi

exit $EVAL_EXIT_CODE
