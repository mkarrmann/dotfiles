#!/bin/bash
# finding_template.sh — emit a Finding JSON skeleton for SendMessage content.
# Usage: finding_template.sh <id> <agent> <claim> [source_kind=native]
set -euo pipefail

ID="${1:?usage: $0 <id> <agent> <claim> [source_kind]}"
AGENT="${2:?usage: $0 <id> <agent> <claim> [source_kind]}"
CLAIM="${3:?usage: $0 <id> <agent> <claim> [source_kind]}"
SOURCE_KIND="${4:-native}"

cat <<JSON
{
  "id": "${ID}",
  "agent": "${AGENT}",
  "claim": "${CLAIM}",
  "evidence": [],
  "confidence": "medium",
  "dependencies": [],
  "needs_diff_landed": false,
  "source_kind": "${SOURCE_KIND}"
}
JSON
