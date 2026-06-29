#!/bin/bash

# https://www.internalfb.com/eval_hawk/DATAMATE/datasets/1580503409778117/records

# This script is used to evaluate the Scuba skill
# Make sure scuba is installed as a skill first!
#
# Usage: ./eval.sh [additional_args...]
#
# To run specific records in the dataset, use:
#   ./eval.sh --evalhawk_dataset_record_ids=875406425492185
#
# Multiple record IDs can be comma-separated:
#   ./eval.sh --evalhawk_dataset_record_ids=875406425492185,123456789

cd /data/sandcastle/boxes/fbsource/fbcode || exit
buck2 run //consumption_ai/evaluation/scripts:evaluator -- \
  --name="Scuba Claude Code Eval" \
  --description="Validates the operation of the Scuba Claude Code Skill" \
  --evalhawk_dataset_id=1580503409778117 \
  --agent=claude_code \
  --pipeline=usecase-dev-ai-ai4d-eval \
  --type=CLAUDE_CODE \
  "$@"
  # If we wanted to only test a subset of the dataset, we could use the following:
  # --evalhawk_dataset_tag=datamate_eval.regression
