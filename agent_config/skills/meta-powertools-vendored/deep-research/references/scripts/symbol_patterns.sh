#!/bin/bash
# symbol_patterns.sh — Language-aware definition pattern for tightened symbol grep.
# Usage: symbol_patterns.sh <symbol_kind> <language> <symbol>
# Outputs: ripgrep regex pattern to stdout, OR exit 1 if no pattern (caller falls back to whole-word).
set -euo pipefail

KIND="${1:?usage: $0 <symbol_kind> <language> <symbol>}"
LANG="${2:?usage: $0 <symbol_kind> <language> <symbol>}"
SYMBOL="${3:?usage: $0 <symbol_kind> <language> <symbol>}"

# Escape regex special chars in SYMBOL
SYMBOL_ESC=$(printf '%s' "$SYMBOL" | sed 's/[][\.*^$()+?{}|/]/\\&/g')

case "${KIND}:${LANG}" in
  function:python)        echo "^\\s*(async\\s+)?def\\s+${SYMBOL_ESC}\\b" ;;
  function:cpp|function:hpp|function:c|function:cc|function:h)
                          echo "\\b\\w[\\w\\s\\*&<>:]*\\b${SYMBOL_ESC}\\s*\\(" ;;
  function:ts|function:tsx|function:js|function:jsx)
                          echo "(function\\s+${SYMBOL_ESC}\\b|const\\s+${SYMBOL_ESC}\\s*=\\s*(async\\s*)?\\(.*?\\)\\s*=>|${SYMBOL_ESC}\\s*:\\s*(async\\s*)?function)" ;;
  function:swift)         echo "\\bfunc\\s+${SYMBOL_ESC}\\b" ;;
  function:kt|function:kotlin)
                          echo "\\bfun\\s+${SYMBOL_ESC}\\b" ;;
  function:rs|function:rust)
                          echo "\\bfn\\s+${SYMBOL_ESC}\\b" ;;
  function:go)            echo "\\bfunc(\\s+\\([^)]+\\))?\\s+${SYMBOL_ESC}\\b" ;;
  function:java)          echo "\\b(public|private|protected|static\\s)+[\\w<>,\\[\\]]+\\s+${SYMBOL_ESC}\\s*\\(" ;;

  class:python)           echo "^\\s*class\\s+${SYMBOL_ESC}\\b" ;;
  class:cpp|class:hpp)    echo "\\b(class|struct)\\s+${SYMBOL_ESC}\\b" ;;
  class:ts|class:tsx)     echo "\\bclass\\s+${SYMBOL_ESC}\\b" ;;
  class:swift)            echo "\\b(class|struct|enum|actor)\\s+${SYMBOL_ESC}\\b" ;;
  class:kt|class:kotlin)  echo "\\b(class|object|interface)\\s+${SYMBOL_ESC}\\b" ;;

  type:python)            echo "^${SYMBOL_ESC}\\s*=\\s*|^\\s*${SYMBOL_ESC}\\s*:\\s*" ;;
  type:cpp|type:hpp)      echo "\\b(typedef|using)\\s+.*\\b${SYMBOL_ESC}\\b" ;;
  type:rs|type:rust)      echo "\\btype\\s+${SYMBOL_ESC}\\b" ;;

  const:python)           echo "^${SYMBOL_ESC}\\s*=" ;;
  const:cpp|const:hpp)    echo "\\b(const|constexpr)\\s+\\S+\\s+${SYMBOL_ESC}\\b" ;;
  const:ts|const:tsx)     echo "^(export\\s+)?const\\s+${SYMBOL_ESC}\\b" ;;

  *) exit 1 ;;  # caller falls back to whole-word
esac
