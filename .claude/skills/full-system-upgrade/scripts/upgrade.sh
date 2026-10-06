#!/usr/bin/env bash
# Drive an interactive `paru -Syu` inside tmux, answering routine prompts
# automatically and handing genuinely consequential ones back to Claude.
#
# Why tmux: paru needs a real tty (pipes break its prompts and progress output),
# and the upgrade must survive if this shell dies. Why a script rather than
# model-driven polling: prompts appear at unpredictable times, and a model that
# sleeps in long blocks leaves the user typing "Y" by hand.
#
# Usage:
#   upgrade.sh start  <workdir> [paru args...]   create session, begin upgrade
#   upgrade.sh watch  <workdir> [max_seconds]    poll+answer; returns on event
#   upgrade.sh answer <workdir> <keys>           send keys, then resume watching
#   upgrade.sh status <workdir>                  one-shot pane tail
#   upgrade.sh stop   <workdir>                  kill the session
#
# `watch` exits with a status line on stdout and these exit codes:
#   0  DONE       upgrade finished (paru exit code included)
#   10 DECISION   a prompt needs a judgement call; context printed
#   11 RUNNING    max_seconds elapsed, still working - just call watch again
#   12 GONE       session vanished unexpectedly
set -uo pipefail

CMD=${1:-}; WORKDIR=${2:-}
[ -z "$CMD" ] || [ -z "$WORKDIR" ] && { echo "usage: upgrade.sh <start|watch|answer|status|stop> <workdir> [...]"; exit 2; }
mkdir -p "$WORKDIR"
SESSION="sysupgrade"
RAWLOG="$WORKDIR/upgrade.raw.log"
DIFFLOG="$WORKDIR/reviewed-diffs.log"

# paru/less emit heavy ANSI + carriage returns; strip both before pattern matching
# so prompts are matched on their plain text.
clean() { sed 's/\r/\n/g' | sed -E 's/\x1b\[[0-9;?]*[a-zA-Z]//g; s/\x1b\][^\x07]*\x07//g'; }
pane()  { tmux capture-pane -p -t "$SESSION" 2>/dev/null | clean; }
lastline() { pane | grep -v '^[[:space:]]*$' | tail -1 | sed 's/[[:space:]]*$//'; }

case "$CMD" in

start)
  shift 2
  tmux kill-session -t "$SESSION" 2>/dev/null
  : > "$RAWLOG"; : > "$DIFFLOG"
  # A wide pane keeps package lists and diffs from wrapping, which makes both
  # prompt matching and later log analysis far more reliable.
  tmux new-session -d -s "$SESSION" -x 240 -y 50 /bin/bash
  tmux set-option -t "$SESSION" remain-on-exit on >/dev/null 2>&1
  # pipe-pane keeps a full transcript even if tmux upgrades itself mid-run, which
  # can break `capture-pane` (new client binary vs old server protocol).
  tmux pipe-pane -o -t "$SESSION" "cat >> $RAWLOG"
  sleep 1
  # Prime sudo first so the auth prompt (password, or Howdy face-unlock) happens
  # at a predictable moment instead of surfacing 20 minutes into an AUR build.
  tmux send-keys -t "$SESSION" 'sudo -v' Enter
  sleep 6
  # The sentinel is built with printf's %s so the *echoed command line* never
  # contains a literal "UPGRADE_DONE_<digit>". Matching on the digit is what
  # distinguishes real completion from the command echo.
  tmux send-keys -t "$SESSION" "paru -Syu $* ; printf 'UPGRADE_DONE_%s\\n' \"\$?\"" Enter
  echo "STARTED session=$SESSION workdir=$WORKDIR"
  ;;

answer)
  KEYS=${3:?keys required}
  if [ "$KEYS" = "__ENTER__" ]; then tmux send-keys -t "$SESSION" Enter
  elif [ "$KEYS" = "__Q__" ];  then tmux send-keys -t "$SESSION" q
  else tmux send-keys -t "$SESSION" "$KEYS" Enter; fi
  echo "SENT $KEYS"
  ;;

status)
  pane | tail -30
  ;;

stop)
  tmux kill-session -t "$SESSION" 2>/dev/null && echo "STOPPED" || echo "NOT RUNNING"
  ;;

watch)
  MAX=${3:-540}
  deadline=$(( $(date +%s) + MAX ))
  last_sig=""; repeat=0
  while :; do
    tmux has-session -t "$SESSION" 2>/dev/null || { echo "GONE session disappeared"; exit 12; }

    p=$(pane)
    # Completion: match the digit, never the echoed printf format string.
    if echo "$p" | grep -qE 'UPGRADE_DONE_[0-9]+'; then
      code=$(echo "$p" | grep -oE 'UPGRADE_DONE_[0-9]+' | tail -1 | grep -oE '[0-9]+$')
      echo "DONE paru_exit=$code"; exit 0
    fi

    line=$(lastline)
    sig=$(echo "$line" | tr -d ' ')
    action=""; reason=""

    # ---- Auto-answer: routine confirmations -----------------------------------
    # These re-ask something the user already decided by asking for an upgrade at
    # all, so answering them is not a judgement call.
    if   echo "$line" | grep -qE '^:: Proceed with installation\? \[Y/n\]'; then action="Y"
    elif echo "$line" | grep -qE '^:: Proceed to review\? \[Y/n\]';         then action="Y"
    elif echo "$line" | grep -qE '^:: Proceed with upgrade\? \[Y/n\]';      then action="Y"
    elif echo "$line" | grep -qE 'Press any key to continue';               then action="__ENTER__"
    # Pager showing a PKGBUILD diff. Quitting the pager is pure UI - the real
    # decision comes at the "Accept changes?" prompt that follows, so capture the
    # diff text first so it can actually be reviewed there.
    elif echo "$line" | grep -qE '^:$|\(END\)|lines [0-9]+-[0-9]+'; then
      { echo "=== diff page captured $(date -Is) ==="; echo "$p"; } >> "$DIFFLOG"
      action="__Q__"

    # ---- Escalate: anything that changes what gets installed ------------------
    # "Accept changes?" follows a PKGBUILD diff. Auto-accepting would rubber-stamp
    # arbitrary AUR build scripts, so a human/model reads the diff instead.
    elif echo "$line" | grep -qE ':: Accept changes\? \[Y/n\]'; then
      reason="PKGBUILD_DIFF_ACCEPT: review the captured diff before accepting"
    elif echo "$line" | grep -qiE 'import PGP key|Trust this key'; then
      reason="PGP_KEY_IMPORT: a new signing key wants trusting"
    elif echo "$line" | grep -qiE 'in conflict.*Remove|Replace .* with '; then
      reason="PACKAGE_REPLACEMENT: a package is being swapped or removed"
    elif echo "$line" | grep -qE 'Enter a number|:: Repository|provider'; then
      reason="PROVIDER_CHOICE: pacman wants a provider/repo picked"
    elif echo "$line" | grep -qE '\[sudo\] password for|Sorry, try again'; then
      reason="SUDO_PASSWORD: interactive authentication needed from the user"
    elif echo "$line" | grep -qE '\[y/N\]'; then
      # A capital-N default is upstream signalling "the safe answer is no".
      reason="DESTRUCTIVE_DEFAULT_NO: prompt defaults to No"
    fi

    if [ -n "$reason" ]; then
      echo "DECISION $reason"
      echo "--- prompt ---"; echo "$line"
      echo "--- pane tail ---"; echo "$p" | grep -v '^[[:space:]]*$' | tail -25
      [ -s "$DIFFLOG" ] && echo "--- captured diffs at: $DIFFLOG ---"
      exit 10
    fi

    if [ -n "$action" ]; then
      # Guard against answering the same prompt forever if a keystroke does not
      # take effect - that means the pattern is wrong, so hand it over.
      if [ "$sig" = "$last_sig" ]; then repeat=$((repeat+1)); else repeat=0; last_sig="$sig"; fi
      if [ "$repeat" -ge 4 ]; then
        echo "DECISION STUCK_PROMPT: answered the same prompt 4x with no change"
        echo "--- prompt ---"; echo "$line"; exit 10
      fi
      "$0" answer "$WORKDIR" "$action" >/dev/null
      sleep 3
      continue
    fi

    [ "$(date +%s)" -ge "$deadline" ] && { echo "RUNNING still working; last: $line"; exit 11; }
    sleep 2
  done
  ;;

*) echo "unknown command: $CMD"; exit 2 ;;
esac
