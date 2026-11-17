#!/usr/bin/env bash
# shellcheck disable=SC2034  # variables below are read by the extracted functions
# Checks bin-macos/startup-windows' fast path: the digest a clean full run
# records (what it covers and what it ignores), when a run takes the fast path,
# and, running the real script against a simulated desktop (stub aerospace,
# browsers, servers and helpers on PATH, HOME in a temp dir), that a login runs
# in full, a run with nothing changed takes the fast path, anything changed or
# changing meanwhile falls back to a full run under the same lock, and runs
# queue on the lock as before.
# Run with: bash tests/test-startup-windows-fast-path.sh
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
STARTUP="${STARTUP_UNDER_TEST:-$ROOT/bin-macos/startup-windows}"
TMP="$(mktemp -d)"
bg_pids=""
cleanup() {
  local pid
  for pid in $bg_pids; do kill "$pid" 2>/dev/null || true; done
  rm -rf "$TMP"
}
trap cleanup EXIT

# Not "failures": that is startup-windows' own count of warnings, which the
# functions under test add to.
failed_checks=0
check() {
  if [[ "$2" == "$3" ]]; then echo "  ok: $1"; else echo "  FAIL: $1 (expected '$2', got '$3')" >&2; failed_checks=$((failed_checks + 1)); fi
}

# ── The simulated desktop ────────────────────────────────────────────
# $SIM/windows: one window a line, "ID<TAB>WS<TAB>APP<TAB>TITLE<TAB>PARENT<TAB>ROOT".
# $SIM/focused: the focused window's id. $SIM/calls: every stub call worth asserting on.
BIN="$TMP/bin"
mkdir -p "$BIN"

cat > "$BIN/aerospace" <<'STUB'
#!/bin/bash
W="$SIM/windows"
cmd="${1:-}"; shift || true
rewrite() { awk -F'\t' -v OFS='\t' "$@" "$W" > "$W.tmp.$$" && mv "$W.tmp.$$" "$W"; }
case "$cmd" in
  list-windows)
    scope=all ws="" json=false
    while [ $# -gt 0 ]; do
      case "$1" in
        --all) scope=all ;;
        --workspace) scope=ws; ws="$2"; shift ;;
        --focused) scope=focused ;;
        --json) json=true ;;
        --format) shift ;;
      esac
      shift
    done
    awk -F'\t' -v scope="$scope" -v ws="$ws" -v focused="$(cat "$SIM/focused" 2>/dev/null)" -v json="$json" '
      function esc(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return s }
      NF >= 6 && (scope == "all" || (scope == "ws" && $2 == ws) || (scope == "focused" && $1 == focused)) {
        if (json == "true")
          out = out (n++ ? "," : "") sprintf("{\"window-id\":%s,\"workspace\":\"%s\",\"app-name\":\"%s\",\"window-title\":\"%s\",\"window-parent-container-layout\":\"%s\",\"workspace-root-container-layout\":\"%s\"}", $1, esc($2), esc($3), esc($4), $5, $6)
        else print $1 " | " $3 " | " $4
      }
      END { if (json == "true") print "[" out "]" }' "$W"
    ;;
  list-apps) cat "$SIM/apps" ;;
  list-monitors) cat "$SIM/monitors" ;;
  list-workspaces)
    case "$*" in
      *monitor-id*) cat "$SIM/wsmonitors" ;;
      *) printf '%s\n' 1 2 3 4 5 6 7 8 T 11 Z ;;
    esac ;;
  move-node-to-workspace)
    wid=$(cat "$SIM/focused" 2>/dev/null)
    if [ "${1:-}" = --window-id ]; then wid="$2"; shift 2; fi
    echo "aerospace move $wid $1" >> "$SIM/calls"
    rewrite -v wid="$wid" -v ws="$1" '$1 == wid { $2 = ws } 1'
    ;;
  focus)
    [ "${1:-}" = --window-id ] && echo "$2" > "$SIM/focused"
    echo "aerospace focus $*" >> "$SIM/calls"
    ;;
  close)
    wid=$(cat "$SIM/focused" 2>/dev/null)
    [ "${1:-}" = --window-id ] && wid="$2"
    echo "aerospace close $wid" >> "$SIM/calls"
    rewrite -v wid="$wid" '$1 != wid'
    ;;
  *) echo "aerospace $cmd $*" >> "$SIM/calls" ;;
esac
exit 0
STUB

# A deep link raises the window pinned to its origin (696), or, with
# $SIM/omnigent-new-window, lands a new one. Each records the lock it ran under:
# the run's pid, and the lock directory's inode and birth time, which a lock let
# go and taken again would change.
cat > "$BIN/open" <<'STUB'
#!/bin/bash
echo "open $*" >> "$SIM/calls"
case "${1:-}" in
  omnigent://*)
    echo "$PPID $(cat "$STARTUP_LOCK_DIR/pid" 2>/dev/null) $(stat -f '%i %B' "$STARTUP_LOCK_DIR" 2>/dev/null)" >> "$SIM/deeplinks"
    if [ -e "$SIM/omnigent-new-window" ]; then
      rm -f "$SIM/omnigent-new-window"
      printf '9001\t1\tOmnigent\tMy chat\tv_accordion\th_tiles\n' >> "$SIM/windows"
      echo 9001 > "$SIM/focused"
    else
      echo 696 > "$SIM/focused"
    fi
    ;;
esac
exit 0
STUB

cat > "$BIN/curl" <<'STUB'
#!/bin/bash
url=""
for a; do case "$a" in http://*|https://*) url="$a" ;; esac; done
port="${url#http://127.0.0.1:}"; port="${port%%/*}"
case "$url" in
  */.well-known/t3/environment)
    [ -e "$SIM/down-$port" ] && exit 7
    echo "{\"environmentId\": \"$(cat "$SIM/env-$port" 2>/dev/null || echo "env-$port")\"}" ;;
  */v1/hosts) echo '{"hosts":[{"name":"mac.example","host_id":"h-mac"}]}' ;;
  */v1/sessions*) echo '{"data":[{"id":"s1","host_id":"h-mac","workspace":"/Users/me","title":"My chat"}]}' ;;
  http://127.0.0.1:*/) [ -e "$SIM/down-$port" ] && exit 7 ;;
esac
exit 0
STUB

# Never more than one at a time: a second one while one runs is a failure.
cat > "$BIN/t3-client-settings" <<'STUB'
#!/bin/bash
if ! mkdir "$SIM/settings.running" 2>/dev/null; then
  echo "t3-client-settings CONCURRENT" >> "$SIM/calls"
  exit 1
fi
echo "t3-client-settings $*" >> "$SIM/calls"
sleep "${SIM_SETTINGS_SLEEP:-0.2}"
rmdir "$SIM/settings.running"
exit 0
STUB

cat > "$BIN/t3-window-scope" <<'STUB'
#!/bin/bash
if [ "${1:-}" = --list-windows ]; then
  echo "t3-window-scope --list-windows" >> "$SIM/calls"
  awk -F'\t' 'NR == FNR { origin[$1] = $2; next } $3 == "Brave Browser" && ($1 in origin) { print $1, origin[$1] }' \
    "$SIM/origins" "$SIM/windows"
  exit 0
fi
echo "t3-window-scope $*" >> "$SIM/calls"
exit 0
STUB

cat > "$BIN/arrange-workspaces" <<'STUB'
#!/bin/bash
echo "arrange-workspaces $*" >> "$SIM/calls"
exit 0
STUB

cat > "$BIN/orchest-open-workspaces" <<'STUB'
#!/bin/bash
echo "orchest-open-workspaces $*" >> "$SIM/calls"
[ ! -e "$SIM/orchest-fails" ]
STUB

cat > "$BIN/osascript" <<'STUB'
#!/bin/bash
case "$*" in
  *"is running"*) echo true ;;
  *NSScreen.screens*) cat "$SIM/screens" ;;
esac
exit 0
STUB

cat > "$BIN/workspaces" <<'STUB'
#!/bin/bash
cat "$SIM/workspaces.json"
STUB

cat > "$BIN/omnigent-server-url" <<'STUB'
#!/bin/bash
case "$1" in
  --port) echo 6767 ;; --tls-port) echo 6443 ;;
  --primary) echo cco.example ;; --standby) echo ftw.example ;;
  --primary-tunnel-port) echo 16767 ;; --standby-tunnel-port) echo 26767 ;;
esac
STUB

# Apps have been up for a day, so nothing waits on a session restore.
printf '#!/bin/bash\ncase "$*" in *etime=*) echo 1-00:00:00 ;; esac\nexit 0\n' > "$BIN/ps"
printf '#!/bin/bash\necho 4242\n' > "$BIN/pgrep"
printf '#!/bin/bash\ncat "$SIM/boot"\n' > "$BIN/sysctl"
printf '#!/bin/bash\necho mac.example\n' > "$BIN/hostname"
printf '#!/bin/bash\nexec /sbin/md5 "$@"\n' > "$BIN/md5"
for stub in lsof; do printf '#!/bin/bash\nexit 1\n' > "$BIN/$stub"; done
for stub in launchctl sketchybar omnigent-app-ensure; do printf '#!/bin/bash\nexit 0\n' > "$BIN/$stub"; done
chmod +x "$BIN"/*

ORIGINS=(
  "1 http://local.t3.localhost:3780 local"
  "2 http://cco-checkout1.t3.localhost:13780 cco-checkout1"
  "3 http://ftw-checkout1.t3.localhost:23780 ftw-checkout1"
  "4 http://cco-checkout2.t3.localhost:13780 cco-checkout2"
  "5 http://ftw-checkout2.t3.localhost:23780 ftw-checkout2"
  "6 http://cco-checkout3.t3.localhost:13780 cco-checkout3"
  "7 http://ftw-checkout3.t3.localhost:23780 ftw-checkout3"
  "8 http://cco-checkout4.t3.localhost:13780 cco-checkout4"
)

# new_desktop DIR -- a desktop in DIR/sim with every window in place and every
# T3 Code origin set up with all three servers, and a HOME in DIR/home.
new_desktop() {
  local dir="$1" sim home entry ws origin label i
  sim="$dir/sim" home="$dir/home"
  mkdir -p "$sim" "$home"
  : > "$sim/calls"
  echo "{ sec = 1000, usec = 0 } Mon Nov 16 06:00:00 2026" > "$sim/boot"
  printf '%s\n' "100 | com.mitchellh.ghostty | Ghostty" "200 | com.google.Chrome | Google Chrome" \
    "300 | com.brave.Browser | Brave Browser" "400 | ai.omnigent.desktop | Omnigent" \
    "500 | com.orchest.desktop | Orchest" "600 | md.obsidian | Obsidian" "700 | com.apple.finder | Finder" > "$sim/apps"
  {
    printf '161\t1\tGhostty\tLocal MacOS\tv_accordion\th_tiles\n'
    printf '201\tT\tGhostty\tFTW: tunnels\tv_accordion\tv_accordion\n'
    printf '204\tT\tGhostty\tCCO: tunnels\tv_accordion\tv_accordion\n'
    i=0
    for entry in "2 CCO: checkout1" "3 FTW: checkout1" "4 CCO: checkout2" "5 FTW: checkout2" "6 CCO: checkout3" "7 FTW: checkout3" "8 CCO: checkout4"; do
      printf '%s\t%s\tGhostty\t%s\tv_accordion\th_tiles\n' "$((210 + i))" "${entry%% *}" "${entry#* }"
      i=$((i + 1))
    done
    for ws in 1 2 3 4 5 6 7 8; do
      printf '%s\t%s\tGoogle Chrome\tNew Tab - Google Chrome - Me\tv_accordion\th_tiles\n' "$((4090 + ws))" "$ws"
      printf '%s\t%s\tBrave Browser\tT3 Code (Beta)\tv_accordion\th_tiles\n' "$((4700 + ws))" "$ws"
      printf '%s\t%s\tOrchest\tWorkspace — Orchest [abcd000%s]\th_tiles\th_tiles\n' "$((4280 + ws))" "$ws" "$ws"
    done
    printf '696\t1\tOmnigent\tMy chat\tv_accordion\th_tiles\n'
    printf '4074\t11\tGoogle Chrome\tCalendar - Google Chrome - Me\th_tiles\tv_tiles\n'
    printf '4087\t11\tGoogle Chrome\tSomeone (DM) - Meta - Slack - Google Chrome - Me\th_tiles\tv_tiles\n'
    printf '4279\t11\tOrchest\tOrchest\th_tiles\tv_tiles\n'
    printf '514\t11\tObsidian\tVault - Obsidian\th_tiles\tv_tiles\n'
    printf '85\tZ\tTodoist\tToday\tv_accordion\tv_accordion\n'
  } > "$sim/windows"
  echo 161 > "$sim/focused"
  # A laptop with one external monitor, the dashboard on the laptop.
  printf '1|DELL U3219Q|1\n2|Built-in Retina Display|2\n' > "$sim/monitors"
  printf '1 0 0 3360 1890\n2 899 -1117 1728 1117\n' > "$sim/screens"
  printf '%s|1\n' 1 2 3 4 5 6 7 8 9 T Z > "$sim/wsmonitors"
  echo "11|2" >> "$sim/wsmonitors"
  : > "$sim/origins"
  for entry in "${ORIGINS[@]}"; do
    read -r ws origin label <<< "$entry"
    echo "$((4700 + ws)) $origin" | tr ' ' '\t' >> "$sim/origins"
    mkdir -p "$home/.local/state/t3-windows/Brave-Browser/$label/.t3-servers"
    : > "$home/.local/state/t3-windows/Brave-Browser/$label/.t3-servers/env-3780"
    : > "$home/.local/state/t3-windows/Brave-Browser/$label/.t3-servers/env-13780"
    : > "$home/.local/state/t3-windows/Brave-Browser/$label/.t3-servers/env-23780"
  done
  mkdir -p "$home/.local/state/t3-pairing/devvm20365.cco0.facebook.com" "$home/.local/state/t3-pairing/devvm36111.ftw0.facebook.com"
  touch -t 202601010000 "$home/.local/state/t3-pairing"/*
  jq -n '{workspaces: [
    {workspace: "1", kind: "local", origin: "local.omnigent.localhost", cwd: "/Users/me", t3Url: "http://local.t3.localhost:3780/", t3Project: "/Users/me/dotfiles"},
    ( [["2","cco","devvm20365.cco0.facebook.com","1",13780], ["3","ftw","devvm36111.ftw0.facebook.com","1",23780],
       ["4","cco","devvm20365.cco0.facebook.com","2",13780], ["5","ftw","devvm36111.ftw0.facebook.com","2",23780],
       ["6","cco","devvm20365.cco0.facebook.com","3",13780], ["7","ftw","devvm36111.ftw0.facebook.com","3",23780],
       ["8","cco","devvm20365.cco0.facebook.com","4",13780]][]
      | {workspace: .[0], kind: "devserver", origin: "\(.[1])-checkout\(.[3]).omnigent.localhost", host: .[2],
         checkout: "/home/me/checkout\(.[3])", nvsSession: "\(.[1] | ascii_upcase)-checkout\(.[3])",
         t3Url: "http://\(.[1])-checkout\(.[3]).t3.localhost:\(.[4])/", t3Project: "~/checkout\(.[3])"} )],
    tlsPort: "6443"}' > "$sim/workspaces.json"
}

# ── 1. The digest ────────────────────────────────────────────────────
echo "== the digest =="
extract_fn() {
  awk -v name="$1" '
    $0 ~ "^" name "\\(\\) \\{" { p = 1 }
    p { print }
    p && $0 == "}" { exit }
  ' "$STARTUP"
}
for fn in steady_snapshot steady_windows steady_titles steady_monitors steady_cheap steady_t3 steady_state t3_pool_state \
          fast_path_applies record_steady_state read_pending_file managed_workspaces find_window_id \
          find_window_id_for_workspace find_chrome_by_title t3_windows t3_workspace_window t3_environment_id; do
  body=$(extract_fn "$fn")
  [[ -n "$body" ]] || { echo "could not extract $fn" >&2; exit 1; }
  eval "$body"
done
eval "$(awk '/^WORKSPACES=\(/{p=1} p{print} /^WORKSPACES=\("\$\{enabled_workspaces\[@\]\}"\)/{exit}' "$STARTUP")"
eval "$(grep -E '^(CHROME_APP|CHROME_INVALID_TITLE_PATTERN|SWEEP_WORKSPACE|T3_TITLE_PATTERN|T3_ANY_TITLE_PATTERN|OMNIGENT_APP|OMNIGENT_INVALID_TITLE_PATTERN|WS11_WORKSPACE|WS11_CAL_TITLE_RE|WS11_SLACK_TITLE_RE|T3_SERVERS_DIR|T3_LOCAL_HOST|T3_PAIRING_FILL_SECONDS|STEADY_SNAPSHOT_FORMAT)=' "$STARTUP")"
T3_BROWSER="Brave Browser"

UNIT="$TMP/unit"
new_desktop "$UNIT"
export SIM="$UNIT/sim"
aerospace() { "$BIN/aerospace" "$@"; }
workspaces() { "$BIN/workspaces" "$@"; }
osascript() { "$BIN/osascript" "$@"; }

echo "-- the monitors"
monitors_base=$(steady_monitors)
check "lists every monitor, workspace and screen" "2 12 2" \
  "$(grep -c '^monitor ' <<< "$monitors_base") $(grep -c '^wsmonitor ' <<< "$monitors_base") $(grep -c '^screen ' <<< "$monitors_base")"
monitors_changed() { # NAME -- after an edit to the files, the digest differs; then put them back
  check "changes it: $1" "yes" "$([[ "$(steady_monitors)" != "$monitors_base" ]] && echo yes || echo no)"
  new_desktop_monitors
}
new_desktop_monitors() {
  printf '1|DELL U3219Q|1\n2|Built-in Retina Display|2\n' > "$SIM/monitors"
  printf '1 0 0 3360 1890\n2 899 -1117 1728 1117\n' > "$SIM/screens"
  printf '%s|1\n' 1 2 3 4 5 6 7 8 9 T Z > "$SIM/wsmonitors"
  echo "11|2" >> "$SIM/wsmonitors"
}
echo "1|Built-in Retina Display|1" > "$SIM/monitors"; echo "1 0 0 1728 1117" > "$SIM/screens"
monitors_changed "the external monitor unplugged"
echo "3|DELL U3219Q (1)|3" >> "$SIM/monitors"; echo "3 3360 -817 1890 3360" >> "$SIM/screens"
monitors_changed "another monitor plugged in"
echo "1 0 0 2560 1440" > "$SIM/screens.1"; sed -n 2p "$SIM/screens" >> "$SIM/screens.1"; mv "$SIM/screens.1" "$SIM/screens"
monitors_changed "a monitor's resolution changed"
sed 's/^3|1$/3|2/' "$SIM/wsmonitors" > "$SIM/wsm" && mv "$SIM/wsm" "$SIM/wsmonitors"
monitors_changed "a workspace moved to another monitor"
check "and back" "$monitors_base" "$(steady_monitors)"

titles_digest() { local s; s=$(steady_snapshot); steady_windows "$s"; steady_titles "$s"; }
edit_windows() { awk -F'\t' -v OFS='\t' "$@" "$SIM/windows" > "$SIM/w" && mv "$SIM/w" "$SIM/windows"; }
BASE_WINDOWS=$(cat "$SIM/windows")
base=$(titles_digest)
reset_windows() { printf '%s\n' "$BASE_WINDOWS" > "$SIM/windows"; }

# changes CASE AWK... -- the digest differs once the windows are edited.
changes() {
  local name="$1"; shift
  reset_windows; edit_windows "$@"
  check "changes it: $name" "yes" "$([[ "$(titles_digest)" != "$base" ]] && echo yes || echo no)"
}
keeps() {
  local name="$1"; shift
  reset_windows; edit_windows "$@"
  check "leaves it: $name" "yes" "$([[ "$(titles_digest)" == "$base" ]] && echo yes || echo no)"
}
changes "a window moved to another workspace" '$1 == 4093 { $2 = 5 } 1'
changes "a window moved to the sweep workspace" '$1 == 4093 { $2 = "Z" } 1'
changes "a new window on a managed workspace" '1; END { print 9100, 3, "Finder", "Docs", "v_accordion", "h_tiles" }'
changes "a Chrome window appearing on an unmanaged workspace" '1; END { print 9101, 9, "Google Chrome", "New Tab - Google Chrome", "h_tiles", "h_tiles" }'
changes "a terminal whose title no longer matches its row" '$1 == 212 { $4 = "zsh" } 1'
changes "a Chrome window that became a profile picker" '$1 == 4093 { $4 = "Who'"'"'s using Chrome?" } 1'
changes "a Chrome window showing a T3 Code page" '$1 == 4093 { $4 = "cco-checkout1.t3.localhost - Network error" } 1'
changes "a T3 Code window on an error page" '$1 == 4703 { $4 = "ftw-checkout1.t3.localhost" } 1'
changes "a layout container change" '$1 == 4093 { $5 = "h_tiles" } 1'
changes "a workspace root layout change" '$2 == 3 { $6 = "v_accordion" } 1'
changes "a Slack window whose title no longer says Slack" '$1 == 4087 { $4 = "Calendar - Google Chrome - Me" } 1'
changes "a profile picker on the sweep workspace" '1; END { print 9102, "Z", "Google Chrome", "Who'"'"'s using Chrome?", "v_accordion", "v_accordion" }'
changes "an Omnigent update overlay" '1; END { print 9103, 1, "Omnigent", "Omnigent Update", "v_accordion", "h_tiles" }'
keeps "a Chrome tab title change" '$1 == 4093 { $4 = "Inbox - Gmail - Google Chrome - Me" } 1'
keeps "an Omnigent conversation title change" '$1 == 696 { $4 = "● Another chat" } 1'
keeps "a Slack unread count" '$1 == 4087 { $4 = "Someone (DM) - Meta - 3 new items - Slack - Google Chrome - Me" } 1'
keeps "windows on the sweep workspace coming and going" '$1 != 85; END { print 9104, "Z", "Google Chrome", "Old tab - Google Chrome", "v_accordion", "v_accordion" }'
keeps "window order in AeroSpace's listing" '{ l[NR] = $0 } END { for (i = NR; i >= 1; i--) print l[i] }'
reset_windows

echo "-- the rows' windows, as the full run's lookups pick them"
row_of() { steady_titles "$(steady_snapshot)" | awk -v ws="$1" '$1 == "row" && $2 == ws { print $4 }'; }
lookup_of() { find_window_id_for_workspace Ghostty "$2" "$1" "$(steady_snapshot)"; }
edit_windows '1; END { print 300, 3, "Ghostty", "CCO: checkout1", "v_accordion", "h_tiles" }'
check "a matching terminal on the row's workspace wins over a lower id elsewhere" "$(lookup_of 2 'CCO.*checkout1\b')" "$(row_of 2)"
check "which is the one on workspace 2" "210" "$(row_of 2)"
edit_windows '$1 == 210 { $2 = "Z" } 1'
check "with none there, the lowest matching id anywhere" "$(lookup_of 2 'CCO.*checkout1\b')" "$(row_of 2)"
edit_windows '$1 == 210 { $2 = 2; $4 = "cco: CHECKOUT1" } 1'
check "matched case-insensitively" "$(lookup_of 2 'CCO.*checkout1\b')|210" "$(row_of 2)|210"
reset_windows
edit_windows '1; END { print 4000, 5, "Google Chrome", "Calendar - Google Chrome - Me", "v_accordion", "h_tiles" }'
ws11=$(steady_titles "$(steady_snapshot)" | grep '^ws11 ')
check "workspace 11's windows are the ones find_chrome_by_title picks" \
  "ws11 cal $(find_chrome_by_title "$WS11_CAL_TITLE_RE" "$(steady_snapshot)") slack $(find_chrome_by_title "$WS11_SLACK_TITLE_RE" "$(steady_snapshot)")" "$ws11"
check "preferring the one on workspace 11 over a lower id" "ws11 cal 4074 slack 4087" "$ws11"
reset_windows

echo "-- servers, setup and token pools"
HOME="$UNIT/home"
T3_STATE_DIR="$HOME/.local/state/t3-windows/Brave-Browser"
T3_PAIRING_DIR="$HOME/.local/state/t3-pairing"
T3_WINDOWS_CACHE="$TMP/t3-windows-cache"
T3_WINDOWS_LISTED="$TMP/t3-windows-listed"
T3_ENV_IDS="$TMP/t3-env-ids"
STARTUP_T3_WINDOWS=1
t3-window-scope() { "$BIN/t3-window-scope" "$@"; }
curl() { "$BIN/curl" "$@"; }
t3_digest() { rm -f "$T3_ENV_IDS"; steady_t3; }
t3_base=$(t3_digest)
check "lists every T3 Code window by origin" "8" "$(grep -c '^t3win ' <<< "$t3_base")"
echo env-cco-2 > "$SIM/env-13780"
check "changes it: a server whose state was reset (new environment id)" "yes" "$([[ "$(t3_digest)" != "$t3_base" ]] && echo yes || echo no)"
rm -f "$SIM/env-13780"; touch "$SIM/down-23780"
check "changes it: a server not answering" "yes" "$([[ "$(t3_digest)" != "$t3_base" ]] && echo yes || echo no)"
rm -f "$SIM/down-23780"
rm "$T3_STATE_DIR/ftw-checkout2/.t3-servers/env-13780"
missing=$(t3_digest)
: > "$T3_STATE_DIR/ftw-checkout2/.t3-servers/env-13780"
check "changes it: an origin gaining a server it is set up with" "yes" "$([[ "$missing" != "$t3_base" ]] && echo yes || echo no)"
check "and back" "$t3_base" "$(t3_digest)"
POOL="$T3_PAIRING_DIR/devvm20365.cco0.facebook.com"
pool_line() { t3_digest | grep "^pool devvm20365"; }
check "an empty pool its tunnel filled long ago is done" "pool devvm20365.cco0.facebook.com 0 done" "$(pool_line)"
rm -rf "$POOL"
check "one its tunnel has not created yet is absent" "pool devvm20365.cco0.facebook.com 0 absent" "$(pool_line)"
mkdir -p "$POOL"; echo TOKEN > "$POOL/token.a"
check "one getting tokens is filling, with a token" "pool devvm20365.cco0.facebook.com 1 filling" "$(pool_line)"
touch -t 202601010000 "$POOL/token.a"
check "one whose last token came long ago is done" "pool devvm20365.cco0.facebook.com 1 done" "$(pool_line)"
rm -f "$POOL/token.a"; touch -t 202601010000 "$POOL"
check "and with that token used, done and empty again" "$t3_base" "$(t3_digest)"

echo "-- when a run takes the fast path"
STEADY_STATE_FILE="$TMP/steady"
STARTUP_PENDING_FILE="$TMP/pending"
STARTUP_PENDING_MAX_AGE_MIN=1440
BASH_SOURCE_SAVED=1
STARTUP_FULL=false
rm -f "$STEADY_STATE_FILE" "$STARTUP_PENDING_FILE"
check "not without a recorded digest (a login, or a run that ended with something to fix)" "no" "$(fast_path_applies && echo yes || echo no)"
steady_state > "$STEADY_STATE_FILE"
check "with the digest unchanged" "yes" "$(fast_path_applies && echo yes || echo no)"
STARTUP_FULL=true
check "not with --full" "no" "$(fast_path_applies && echo yes || echo no)"
STARTUP_FULL=false
echo "5|t3" > "$STARTUP_PENDING_FILE"
check "not while a row waits on a tunnel" "no" "$(fast_path_applies && echo yes || echo no)"
rm -f "$STARTUP_PENDING_FILE"
eval "real_$(declare -f steady_t3)"
steady_t3() { echo x >> "$TMP/t3-calls"; real_steady_t3; }
: > "$TMP/t3-calls"
edit_windows '$1 == 4093 { $2 = 5 } 1'
check "not with a window moved, which it tells without asking T3 Code's browser or servers" "no 0" \
  "$(fast_path_applies && echo yes || echo no) $(wc -l < "$TMP/t3-calls" | tr -d ' ')"
reset_windows
echo env-cco-2 > "$SIM/env-13780"
rm -f "$T3_ENV_IDS"  # a new run asks the servers afresh
check "not with a server reset, which takes asking them" "no 1" \
  "$(fast_path_applies && echo yes || echo no) $(wc -l < "$TMP/t3-calls" | tr -d ' ')"
rm -f "$SIM/env-13780" "$T3_ENV_IDS"
eval "$(declare -f real_steady_t3 | sed '1s/real_steady_t3/steady_t3/')"

echo "-- what a full run records"
STEADY_WARNINGS_FILE="$STEADY_STATE_FILE.warnings"
steady_state() { echo "STATE"; }
record() { # FAST FAILURES PENDING UNREACHABLE [TOKEN_WARNING...]
  fast_path="$1" failures="$2" pending_rows="$3" t3_unreachable_hosts="$4"
  shift 4
  t3_token_warnings=("$@")
  record_steady_state
}
rm -f "$STEADY_STATE_FILE" "$STEADY_WARNINGS_FILE"
record false 0 "" ""
check "a clean run records its digest, with no warnings" "STATE|" "$(cat "$STEADY_STATE_FILE")|$(cat "$STEADY_WARNINGS_FILE")"
record false 1 "" "" "WARNING: [T3: ws5] no pairing token from cco"
check "a run whose only warnings wait on a token records them too" "STATE|WARNING: [T3: ws5] no pairing token from cco" \
  "$(cat "$STEADY_STATE_FILE")|$(cat "$STEADY_WARNINGS_FILE")"
record false 2 "" "" "WARNING: [T3: ws5] no pairing token from cco"
check "a run with any other warning forgets the last digest" "no no" \
  "$([[ -e "$STEADY_STATE_FILE" ]] && echo yes || echo no) $([[ -e "$STEADY_WARNINGS_FILE" ]] && echo yes || echo no)"
record false 0 "" ""
record false 0 "1|omnigent" ""
check "as does one that left a row for a tunnel" "no" "$([[ -e "$STEADY_STATE_FILE" ]] && echo yes || echo no)"
record false 0 "" ""
record false 0 "" "devvm20365.cco0.facebook.com"
check "or found a server not answering" "no" "$([[ -e "$STEADY_STATE_FILE" ]] && echo yes || echo no)"
echo "RECORDED" > "$STEADY_STATE_FILE"
record true 3 "" ""
check "a fast run leaves the digest as it was" "RECORDED" "$(cat "$STEADY_STATE_FILE")"
unset -f aerospace workspaces t3-window-scope curl osascript
unset SIM

# ── 2. The real script on a simulated desktop ────────────────────────
echo "== whole runs on a simulated desktop =="

# use_desk DIR -- point the helpers below at the desktop in DIR.
use_desk() {
  DESK="$1" SIM="$1/sim" SANDBOX="$1/home" LOCK="$1/lock"
  STEADY="$SANDBOX/.local/state/startup-windows-steady"
  PENDING="$SANDBOX/.local/state/startup-windows-pending"
}
# run_sw [ARGS...] -- the real script in a clean environment: only the stubs
# and the system's own tools on PATH, HOME in the sandbox.
run_sw() {
  env -i HOME="$SANDBOX" PATH="$BIN:/usr/bin:/bin" SIM="$SIM" TMPDIR="$DESK" \
    STARTUP_LOCK_DIR="$LOCK" STARTUP_WINDOWS_TIMESTAMPS=0 T3_LOCAL_SERVER_WAIT=0 \
    SIM_SETTINGS_SLEEP="${SIM_SETTINGS_SLEEP:-0.2}" "$STARTUP" "$@"
}
calls() { grep -c -- "$1" "$SIM/calls" || true; }
fresh_calls() { : > "$SIM/calls"; rm -f "$SIM/deeplinks"; }
fast_taken() { grep -q 'Nothing a full run fixes changed' "$1" && echo fast || echo full; }
exists() { [[ -e "$1" ]] && echo yes || echo no; }
wait_for_output() {
  for _ in $(seq 1 200); do
    grep -Fq "$2" "$1" 2>/dev/null && return 0
    sleep 0.05
  done
  return 1
}

echo "-- a login: no digest yet"
use_desk "$TMP/login"
new_desktop "$DESK"
fresh_calls
run_sw > "$DESK/run.out" 2>&1 || true
check "runs in full" "full" "$(fast_taken "$DESK/run.out")"
check "reconciling the rows" "yes" "$(grep -q '\[Chrome: ws3\]' "$DESK/run.out" && grep -q '\[T3: ws3\] assigning T3 Code window' "$DESK/run.out" && echo yes || echo no)"
check "laying out workspaces 1-8 and T" "1" "$(calls 'arrange-workspaces 1 2 3 4 5 6 7 8 T')"
check "ending clean" "Done." "$(tail -n 1 "$DESK/run.out")"
check "and recording its digest" "yes" "$([[ -s "$STEADY" ]] && echo yes || echo no)"
check "leaving no lock behind" "no" "$(exists "$LOCK")"
[[ "$(tail -n 1 "$DESK/run.out")" == "Done." ]] || { echo "--- login run output:"; cat "$DESK/run.out"; } >&2
STEADY_DESK="$DESK"

# Each group below starts from a copy of that desktop, the digest recorded, and
# runs alongside the others; its checks print to its own report.
group_steady() {
  local recorded rc
  recorded=$(cat "$STEADY")
  echo "-- nothing changed since"
  fresh_calls
  rc=0; run_sw > "$DESK/run2.out" 2>&1 || rc=$?
  check "takes the fast path" "fast" "$(fast_taken "$DESK/run2.out")"
  check "exiting 0, clean" "0 Done." "$rc $(tail -n 1 "$DESK/run2.out")"
  check "with no Chrome, T3 Code or terminal row" "0" "$(grep -cE '^\[(Chrome|T3): ws|^\[(CCO|FTW): checkout|^\[Local MacOS\]|\[ws11\] reserved' "$DESK/run2.out" || true)"
  check "still pinning Omnigent's window" "1" "$(calls 'open omnigent://local.omnigent.localhost:6443/c/s1')"
  check "checking Orchest's placement" "1" "$(calls 'orchest-open-workspaces --prod')"
  check "arranging workspace 11" "1" "$(calls 'arrange-workspaces --ensure-windows 11')"
  check "syncing T3 Code's client settings" "1" "$(calls 't3-client-settings')"
  check "but not laying out workspaces 1-8 and T" "0" "$(calls 'arrange-workspaces 1 2')"
  check "moving no window but onto the workspace it is on (the tunnel checks before, Omnigent's pin)" \
    "aerospace move 201 T|aerospace move 204 T|aerospace move 696 1" "$(grep 'aerospace move' "$SIM/calls" | sort | paste -sd '|' -)"
  check "nor listing T3 Code's windows again" "0" "$(calls 't3-window-scope --list-windows')"
  check "leaving the digest as it was" "$recorded" "$(cat "$STEADY")"
  [[ "$(fast_taken "$DESK/run2.out")" == fast ]] || { echo "--- run output:"; cat "$DESK/run2.out"; } >&2

  echo "-- a window changed between runs"
  awk -F'\t' -v OFS='\t' '$1 == 4093 { $2 = "Z" } 1' "$SIM/windows" > "$SIM/w" && mv "$SIM/w" "$SIM/windows"
  fresh_calls
  run_sw > "$DESK/run3.out" 2>&1 || true
  check "a Chrome window moved off its workspace: a full run, which puts it back" "full 4093 3" \
    "$(fast_taken "$DESK/run3.out") $(awk -F'\t' '$1 == 4093 { print $1, $2 }' "$SIM/windows")"
  printf '9200\t4\tFinder\tDocuments\tv_accordion\th_tiles\n' >> "$SIM/windows"
  fresh_calls
  run_sw > "$DESK/run4.out" 2>&1 || true
  check "a stray window on a managed workspace: a full run, which sweeps it" "full Z" \
    "$(fast_taken "$DESK/run4.out") $(awk -F'\t' '$1 == 9200 { print $2 }' "$SIM/windows")"
  fresh_calls
  run_sw > "$DESK/run5.out" 2>&1 || true
  check "after which the fast path again" "fast" "$(fast_taken "$DESK/run5.out")"

  echo "-- a monitor unplugged between runs"
  echo "2|Built-in Retina Display|1" > "$SIM/monitors"
  echo "1 0 0 1728 1117" > "$SIM/screens"
  sed 's/|1$/|2/' "$SIM/wsmonitors" > "$SIM/wsm" && mv "$SIM/wsm" "$SIM/wsmonitors"
  fresh_calls
  run_sw > "$DESK/run-monitor.out" 2>&1 || true
  check "a full run, which lays out workspaces 1-8 and T for it" "full 1" \
    "$(fast_taken "$DESK/run-monitor.out") $(calls 'arrange-workspaces 1 2 3 4 5 6 7 8 T')"
  fresh_calls
  run_sw > "$DESK/run-monitor2.out" 2>&1 || true
  check "after which the fast path again" "fast" "$(fast_taken "$DESK/run-monitor2.out")"
}

group_fallback() {
  local rc
  echo "-- the deep link lands another window during a fast run"
  touch "$SIM/omnigent-new-window"
  fresh_calls
  rc=0; run_sw > "$DESK/run6.out" 2>&1 || rc=$?
  check "the fast run falls back to a full one" "yes" "$(grep -q 'Windows changed meanwhile; doing a full run.' "$DESK/run6.out" && echo yes || echo no)"
  check "which runs the rows and ends clean" "0 yes Done." \
    "$rc $(grep -q '\[Chrome: ws3\]' "$DESK/run6.out" && echo yes || echo no) $(tail -n 1 "$DESK/run6.out")"
  check "and sweeps the extra Omnigent window" "Z" "$(awk -F'\t' '$1 == 9001 { print $2 }' "$SIM/windows")"
  check "pinning twice, both in the same process" "2 1" \
    "$(wc -l < "$SIM/deeplinks" | tr -d ' ') $(awk '{ print $1 }' "$SIM/deeplinks" | sort -u | wc -l | tr -d ' ')"
  check "under the same lock, never let go: same pid in it, same directory" "1" \
    "$(awk '{ print $2, $3, $4 }' "$SIM/deeplinks" | sort -u | wc -l | tr -d ' ')"
  check "whose pid is the run's" "yes" "$(awk '$1 == $2 { n++ } END { print (n == NR && NR > 0) ? "yes" : "no" }' "$SIM/deeplinks")"
  check "with T3 Code's client settings never synced twice at once" "0 2" "$(calls CONCURRENT) $(calls 't3-client-settings')"
  check "and no lock left behind" "no" "$(exists "$LOCK")"
  fresh_calls
  run_sw > "$DESK/run7.out" 2>&1 || true
  check "the full run recorded the new desktop: the next run is fast" "fast" "$(fast_taken "$DESK/run7.out")"
}

group_reasons() {
  local label
  echo "-- other reasons for a full run"
  echo "1|omnigent" > "$PENDING"
  fresh_calls
  run_sw > "$DESK/run8.out" 2>&1 || true
  check "a row waiting on a tunnel" "full" "$(fast_taken "$DESK/run8.out")"
  echo env-ftw-2 > "$SIM/env-23780"
  for label in local cco-checkout1 ftw-checkout1 cco-checkout2 ftw-checkout2 cco-checkout3 ftw-checkout3 cco-checkout4; do
    mv "$SANDBOX/.local/state/t3-windows/Brave-Browser/$label/.t3-servers/env-23780" \
       "$SANDBOX/.local/state/t3-windows/Brave-Browser/$label/.t3-servers/env-ftw-2"
  done
  fresh_calls
  run_sw > "$DESK/run9.out" 2>&1 || true
  check "a server with a new environment id" "full" "$(fast_taken "$DESK/run9.out")"
  fresh_calls
  run_sw --full > "$DESK/run10.out" 2>&1 || true
  check "--full" "full" "$(fast_taken "$DESK/run10.out")"
  echo "{ sec = 2000, usec = 0 } Tue Nov 17 06:00:00 2026" > "$SIM/boot"
  fresh_calls
  run_sw > "$DESK/run11.out" 2>&1 || true
  check "a reboot, even with every window id the same" "full" "$(fast_taken "$DESK/run11.out")"
}

group_warnings() {
  local started
  echo "-- warnings"
  touch "$SIM/orchest-fails"
  fresh_calls
  run_sw --full > "$DESK/run12.out" 2>&1 || true
  check "a full run with a warning a rerun might fix forgets the digest" "full no" \
    "$(fast_taken "$DESK/run12.out") $(exists "$STEADY")"
  rm -f "$SIM/orchest-fails"
  rm "$SANDBOX/.local/state/t3-windows/Brave-Browser/ftw-checkout2/.t3-servers/env-13780"
  fresh_calls
  started=$SECONDS
  run_sw > "$DESK/run14.out" 2>&1 || true
  check "so the next run is full; one whose only warning is a link waiting on a token (without waiting for one)" "full 1 yes" \
    "$(fast_taken "$DESK/run14.out") $(grep -c 'no pairing token from devvm20365.cco0.facebook.com' "$DESK/run14.out") $( (( SECONDS - started < 20 )) && echo yes || echo no)"
  check "still records its digest" "yes" "$([[ -s "$STEADY" ]] && echo yes || echo no)"
  fresh_calls
  run_sw > "$DESK/run15.out" 2>&1 || true
  check "and the next run is fast, repeating that warning" "fast 1 Done with 1 warning(s)." \
    "$(fast_taken "$DESK/run15.out") $(grep -c 'no pairing token from devvm20365.cco0.facebook.com.*as the last full run found' "$DESK/run15.out") $(tail -n 1 "$DESK/run15.out")"
  mkdir -p "$SANDBOX/.local/state/t3-pairing/devvm20365.cco0.facebook.com"
  echo TOKEN > "$SANDBOX/.local/state/t3-pairing/devvm20365.cco0.facebook.com/token.a"
  fresh_calls
  run_sw > "$DESK/run16.out" 2>&1 || true
  check "until its tunnel pools a token: then a full run, which links it" "full 1 yes" \
    "$(fast_taken "$DESK/run16.out") $(calls 't3-window-scope @chrome http://ftw-checkout2.t3.localhost:23780 --link-only') $(exists "$SANDBOX/.local/state/t3-windows/Brave-Browser/ftw-checkout2/.t3-servers/env-13780")"
}

group_concurrent() {
  local slow deferred
  echo "-- runs at the same time"
  fresh_calls
  SIM_SETTINGS_SLEEP=3 run_sw > "$DESK/slow.out" 2>&1 &
  slow=$!
  wait_for_output "$DESK/slow.out" "Nothing a full run fixes changed" || true
  check "(a slow fast run holds the lock)" "yes" "$([[ -s "$LOCK/pid" ]] && echo yes || echo no)"
  run_sw > "$DESK/second.out" 2>&1 || true
  check "a second full run exits, leaving it to the running one" "yes" \
    "$(grep -q 'startup-windows is already running as PID' "$DESK/second.out" && echo yes || echo no)"
  run_sw --deferred > "$DESK/deferred.out" 2>&1 &
  deferred=$!
  wait_for_output "$DESK/deferred.out" "Waiting for startup-windows" || true
  check "a --deferred run queues behind the fast run" "yes running" \
    "$(grep -q 'Waiting for startup-windows' "$DESK/deferred.out" && echo yes || echo no) $(kill -0 "$slow" 2>/dev/null && echo running || echo done)"
  wait "$slow" || true
  wait "$deferred" || true
  check "and goes ahead once it is done (finding nothing left for it)" "yes" "$(grep -q 'No deferred windows.' "$DESK/deferred.out" && echo yes || echo no)"
  check "the fast run having ended clean" "Done." "$(tail -n 1 "$DESK/slow.out")"
  check "with no lock left behind" "no" "$(exists "$LOCK")"
}

groups=(group_steady group_fallback group_reasons group_warnings group_concurrent)
for group in "${groups[@]}"; do
  rm -rf "$TMP/$group"
  cp -Rp "$STEADY_DESK" "$TMP/$group"
  ( use_desk "$TMP/$group"; "$group" ) > "$TMP/$group.report" 2>&1 &
  bg_pids+=" $!"
done
wait
for group in "${groups[@]}"; do
  cat "$TMP/$group.report"
  failed_checks=$((failed_checks + $(grep -c '^  FAIL' "$TMP/$group.report" || true)))
done

if [[ "$failed_checks" -gt 0 ]]; then
  echo "$failed_checks check(s) failed" >&2
  exit 1
fi
echo "startup-windows fast path tests passed"
