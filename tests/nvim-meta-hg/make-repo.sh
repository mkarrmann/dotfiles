#!/usr/bin/env bash
set -euo pipefail

# Builds a throwaway Sapling repo for the nvim meta-hg specs and prints
# name=hash lines for the commits a spec may want to address.
#
#   make-repo.sh <dir> diff    base -> target -> edge -> many -> descendant (checked out),
#                              plus uncommitted edits and an uncommitted rename
#   make-repo.sh <dir> plain   one commit of a 20-line file and a small file

dir=$1
kind=$2
mkdir -p "$dir"
cd "$dir"
sl init --config init.prefer-git=false . >/dev/null 2>&1
export HGUSER=test

rev() { echo "$1=$(sl log -r . -T '{node|short}')"; }

case $kind in
  diff)
    printf 'export const a = 1;\n' > foo.ts
    printf 'export const b = <div/>;\n' > foo.tsx
    printf 'def old():\n    return 1\n' > old_name.py
    printf 'print("bar")\n' > bar.py
    printf 'x = 1\n' > wc_old.py
    printf 'a\r\nb\r\nc\r\n' > dos.txt
    sl add -q . && sl commit -q -m base
    rev base
    printf 'a = 1\n' > a.py && sl add -q a.py
    printf 'export const a = 2;\n' > foo.ts
    printf 'export const b = <span/>;\n' > foo.tsx
    sl mv -q old_name.py new_name.py && printf 'def old():\n    return 2\n' > new_name.py
    printf '#!/bin/bash\necho hi\n' > runme && sl add -q runme
    sl commit -q -m target
    rev target
    printf 'a\r\nb\r\n' > crlf.txt
    printf 'no newline' > nonl.txt
    : > empty.txt
    printf 'bin\x00ary\x00\n' > bin.dat
    printf 'glob\n' > 'we[i]rd*.txt'
    sl cp -q a.py a_copy.py
    sl add -q crlf.txt nonl.txt empty.txt bin.dat 'we[i]rd*.txt' && sl commit -q -m edge
    rev edge
    mkdir many && for i in $(seq -w 1 120); do echo "v$i" > "many/f$i.txt"; done
    sl add -q many && sl commit -q -m many
    rev many
    printf 'print("bar2")\n' > bar.py && sl commit -q -m descendant
    rev descendant
    printf 'print("bar3")\n' > bar.py
    printf 'a\r\nB\r\nc\r\n' > dos.txt
    sl mv -q wc_old.py wc_new.py && printf 'x = 2\n' > wc_new.py
    ;;
  plain)
    seq -f 'line %g' 1 20 > lines.txt
    printf 'small\n' > small.txt
    sl add -q . && sl commit -q -m plain
    rev plain
    ;;
  *)
    echo "make-repo.sh: unknown kind '$kind'" >&2
    exit 2
    ;;
esac
