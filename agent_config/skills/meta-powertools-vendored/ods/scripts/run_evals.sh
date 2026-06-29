#!/bin/bash
# Run MSL Judge evals for the ODS skill (meta ods) using the shared ods-cli eval suites.
# SkillWatch Dashboard: https://skillwatch.nest.x2p.facebook.net/dashboard?project=ods-cli-evals
#
# This script reuses the eval suites from ods-cli (which are tool-agnostic) and points
# them at the ods skill directory. The intercept_patterns in the evals cover both
# "ods" and "meta ods", so the same tests work for both skills.
#
# NOTE: Local runs populate the SkillWatch dashboard by default, unless you
# explicitly pass --no-db. The --project flag tells SkillWatch where to file
# the results (this script defaults to "ods-evals").
#
# Usage:
#   ./run_evals.sh                       # Run all evals (default)
#   ./run_evals.sh run-all               # Run all evals
#   ./run_evals.sh run-suite <suite>     # Run a specific suite
#   ./run_evals.sh run <test-id>         # Run a single test
#   ./run_evals.sh list                  # List all available tests
#
# A/B Testing:
#   ./run_evals.sh run-ab --ab-test-b-side-commit <hash>              # A/B test all evals
#   ./run_evals.sh run-ab <suite> --ab-test-b-side-commit <hash>      # A/B test a suite
#   ./run_evals.sh run-ab --ab-test-b-side-no-skill                   # A/B test: skill vs no-skill
#
# Auto-Discovery:
#   ./run_evals.sh run-auto <path>                    # Auto-discover and run evals from a path
#   ./run_evals.sh run-auto <path> -s "canvas-.*"     # Filter suites by regex
#   ./run_evals.sh run-auto <path> -t "cr-"           # Filter test IDs by regex
#
# Options (pass after the command):
#   --judge-rounds N    Number of judge rounds for majority voting (default: 3)
#   --workers N         Parallel workers (default: 4)
#   --verbose           Show detailed output
#   --show-judge-details Show agent response and judge feedback
#   --no-db             Disable database logging (prevents populating the dashboard)
#   --project NAME      SkillWatch project name for filing results (default: ods-evals)
#   --output FILE       Save results to JSON file
#   --no-analyze        Skip post-eval failure analysis and recommendations
#
# Available suites (shared from ods-cli):
#   canvas-urls         Canvas URL handling (--from_url, short URLs, time range preservation)
#   entity-discovery    Entity resolution and key discovery (resolve, eki)
#   query-construction  Query building (transforms, reductions, time ranges, datatype)
#   smc-selectors       SMC selector patterns (recursive, filters, selector types)
#   diagnostics         Troubleshooting workflows (wtf, no-data, regex)
#   critical-rules      Safety and behavioral rules (no-WebFetch, resolve-first, reductions)
#   end-to-end          Multi-step investigation workflows
#
# Examples:
#   ./run_evals.sh run-all --judge-rounds 1 --workers 2
#   ./run_evals.sh run-suite critical-rules --verbose --show-judge-details
#   ./run_evals.sh run cr-no-webfetch-001 --verbose
#   ./run_evals.sh run-all --no-db
#   ./run_evals.sh list

set -euo pipefail

# Resolve paths relative to this script's location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# Reuse eval suites from ods-cli (they are tool-agnostic)
EVAL_SUITES_DIR="$(cd "$SKILL_DIR/../ods-cli/eval_suites" && pwd)"

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
        COMMAND="run-all"
        ;;
esac

# For 'run' and 'run-suite', capture the required argument
TARGET_ARG=""
case "$COMMAND" in
    run|run-suite)
        if [[ $# -gt 0 && ! "$1" =~ ^-- ]]; then
            TARGET_ARG="$1"
            shift
        else
            echo "Error: '$COMMAND' requires an argument (test ID or suite name)."
            echo "Available suites: canvas-urls, entity-discovery, query-construction, smc-selectors, diagnostics, critical-rules, end-to-end"
            exit 1
        fi
        ;;
    run-ab)
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
        exit 1
    fi
fi

# Handle project management commands
case "$COMMAND" in
    create-project|check-project|delete-project)
        PROJECT_CMD="${COMMAND%-project}"
        echo "============================================"
        echo "  ODS Skill (meta ods) - Project Management"
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
echo "  ODS Skill Evals — meta ods (SkillWatch)"
echo "============================================"
echo "Command:        $COMMAND${TARGET_ARG:+ $TARGET_ARG}"
if [[ "$COMMAND" == "run-ab" ]]; then
    echo "Mode:           A/B Test"
fi
echo "Skill dir:      $SKILL_DIR"
echo "Eval suites:    $EVAL_SUITES_DIR (shared from ods-cli)"
echo "Judge rounds:   $JUDGE_ROUNDS"
echo "Workers:        $WORKERS"
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    echo "Extra args:     ${EXTRA_ARGS[*]}"
fi
echo "============================================"
echo ""

# Handle utility commands
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

if [[ -n "$TARGET_ARG" ]]; then
    CMD+=("$TARGET_ARG")
fi

CMD+=(
    --eval-suites-dir "$EVAL_SUITES_DIR"
    --skill-dir "$SKILL_DIR"
    --judge-rounds "$JUDGE_ROUNDS"
    --workers "$WORKERS"
    --show-judge-details
)

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    CMD+=("${EXTRA_ARGS[@]}")
fi

echo "Running: ${CMD[*]}"
echo ""

# Capture output for post-eval analysis
EVAL_OUTPUT_FILE=$(mktemp /tmp/ods-eval-output.XXXXXX)
trap 'rm -f "$EVAL_OUTPUT_FILE"' EXIT

"${CMD[@]}" 2>&1 | tee "$EVAL_OUTPUT_FILE"
EVAL_EXIT_CODE=${PIPESTATUS[0]}

# Post-eval failure analysis
if [[ "$ANALYZE" == true && "$COMMAND" != "list" && $EVAL_EXIT_CODE -eq 0 ]]; then
    if grep -qE '(FAIL|score.*0\.[0-9]|Failed: [1-9])' "$EVAL_OUTPUT_FILE"; then
        echo ""
        echo "============================================"
        echo "  Analyzing failing signals..."
        echo "============================================"
        echo ""

        ANALYSIS_PROMPT="You are analyzing ODS skill (meta ods) eval results to recommend SKILL.md improvements.

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
