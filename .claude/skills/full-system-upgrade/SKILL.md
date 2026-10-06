---
name: full-system-upgrade
description: Run, monitor, analyze, and fix a full Arch Linux system upgrade with paru/pacman. Drives the upgrade inside tmux, answers routine prompts automatically, escalates consequential ones, triages the log into real issues vs. build noise, verifies system health afterwards, and applies or proposes fixes. Use this whenever the user asks to upgrade, update, or patch their system — including "full system upgrade", "run paru -Syu", "update my packages", "upgrade the system", "is anything out of date", "sync and upgrade", or any request to bring an Arch machine up to date. Prefer this over running paru or pacman directly, since a bare upgrade command leaves prompts unanswered and errors untriaged.
---

# Full system upgrade

An Arch upgrade is easy to *start* and easy to get wrong afterwards. The failure
modes this workflow exists to prevent:

- **Blocking on prompts.** `paru -Syu` stops for confirmations, PKGBUILD diffs and
  sudo re-auth at unpredictable moments. Polling with long sleeps means the user
  ends up typing `Y` by hand while you wait — the thing they delegated.
- **Drowning real errors in noise.** 150 packages with AUR builds emit hundreds of
  lines containing "error" or "warning" that mean nothing. Reporting them all is
  worse than reporting none: it teaches the user to skim past your output.
- **Blaming the upgrade for pre-existing breakage.** Half the failed units you'll
  find were already failing. Without a baseline you cannot tell, and you'll waste
  the user's attention on a scare.

Work through the phases below. Scripts live in `scripts/`, referenced by absolute
path from this skill's directory.

---

## Phase 0 — Preflight

```bash
bash <skill>/scripts/preflight.sh | tee <workdir>/baseline.txt
```

Use a scratch `<workdir>` outside the user's project. **Keep `baseline.txt`** —
Phase 3 diffs against it, and that diff is what separates "the upgrade broke this"
from "this was already broken."

Exit code 1 means a hard blocker (no power, no disk, stale DB lock). Report it and
stop; a half-applied upgrade is far worse than a delayed one. Warnings are worth
mentioning but don't stop the run.

## Phase 1 — Run and monitor

```bash
bash <skill>/scripts/upgrade.sh start <workdir>
bash <skill>/scripts/upgrade.sh watch <workdir> 540
```

`watch` polls every 2s and answers routine prompts itself. It returns when
something needs you, with these exit codes:

| Code | Meaning | What to do |
|---|---|---|
| 0 | `DONE paru_exit=N` | Move to Phase 2. Non-zero `paru_exit` → triage.md §9/§10 |
| 10 | `DECISION <reason>` | Decide, then `upgrade.sh answer <workdir> <keys>` and call `watch` again |
| 11 | `RUNNING` | Still working; just call `watch` again |
| 12 | `GONE` | Session died — inspect `<workdir>/upgrade.raw.log` |

Call `watch` in a loop until it returns 0. Don't substitute your own `sleep`-based
polling; the script exists precisely because that approach failed in practice.

**Handling `DECISION`.** The watcher auto-answers only prompts the user already
implicitly approved by asking for an upgrade (`Proceed with installation?`,
`Proceed to review?`, paging through a diff). Everything that changes *what* gets
installed comes to you:

- `PKGBUILD_DIFF_ACCEPT` — read the captured diff at
  `<workdir>/reviewed-diffs.log` before answering. Checksum and version bumps on
  an existing source URL are routine; a changed `source=` host, a new
  `install=` script, or added network calls in `prepare()`/`build()` deserve a
  pause and a word to the user. Reviewing is the entire point of the prompt —
  answering `Y` without reading it is worse than disabling review.
- `PGP_KEY_IMPORT`, `PACKAGE_REPLACEMENT`, `PROVIDER_CHOICE` — trust and
  package-set decisions. Summarise what's being asked and get the user's call.
- `SUDO_PASSWORD` — needs a human. Tell the user to run
  `tmux attach -t sysupgrade`, authenticate, then detach with `Ctrl-b d`.
- `STUCK_PROMPT` — a pattern didn't take. Read the pane and drive it manually
  with `answer`.

## Phase 2 — Analyze the log

```bash
bash <skill>/scripts/analyze-log.sh <workdir>/upgrade.raw.log
```

Output is tiered:

- **CRITICAL** — the upgrade didn't fully succeed. Address before anything else.
- **NEEDS ATTENTION** — it succeeded, but something wants a decision.
- **TRANSACTION SUMMARY / HOOKS** — what actually happened.
- **SUPPRESSED** — known-benign build noise, with a count so nothing is hidden.

The suppression list is deliberate, not laziness — `references/triage.md` §1 has
the catalogue and why each entry is noise. If something looks important but got
suppressed, grep the clean log directly rather than widening the filter blindly.

For anything in the top two tiers, look it up in `references/triage.md` (contents
list at the top) rather than improvising a diagnosis.

## Phase 3 — Verify system health

```bash
bash <skill>/scripts/postcheck.sh <workdir>/baseline.txt
```

Always pass the baseline. The script reports failed units and `.pacnew` files
split into **NEW** (this upgrade) and **pre-existing** — say which is which in
your report, explicitly.

It also determines whether a reboot is genuinely required: on a kernel upgrade
Arch removes the running kernel's modules tree, so anything not already loaded
can no longer load. That's a concrete consequence, not a ritual — state it that way.

Then check whatever this machine specifically depends on. `references/known-quirks.md`
lists them (Howdy's `cv2`/`dlib` imports, `hid-xpadneo` DKMS, the razer user
units, why `systemd-resolved` failing is expected here). Read it before
concluding — several findings that look alarming are known and benign, and one
(`razer-cli read fan`) will hang your shell if you try the obvious check.

## Phase 4 — Fix

Two tiers. The split is about blast radius and reversibility, not confidence.

**SAFE — apply, then report what you did.** Reversible, touches no config and no
package set: DKMS rebuilds for the installed kernel, font/icon/desktop cache
refreshes, restarting user services or stateless daemons, keyring refresh,
clearing a corrupt package from the cache.

**ASK — propose the exact command, then wait.** Anything that removes or installs
packages, edits `/etc`, or touches boot, kernel, PAM or authentication. Includes
orphan removal, all `.pacnew` merges, mkinitcpio changes, and AUR rebuilds.

Present ASK items as a short list with the command and the one-line reason, so the
user can approve some and decline others. Don't bundle them into a single
yes/no — that pressures an all-or-nothing answer on unrelated changes.

Two standing cautions: never blind-merge `/etc/pam.d/sudo` (Howdy lives there and
a bad merge can lock the user out), and never pipe `pacman -Qdtq` into `-Rns`
(plenty of orphans are deliberate).

If the explicit package set changed, the dotfiles repo expects its lists
refreshed — see `references/known-quirks.md`. Usually it hasn't; check before
offering.

## Phase 5 — Report

Lead with the outcome, then what needs the user. Keep benign findings brief and
grouped — their purpose is to show you checked, not to fill space.

```markdown
Upgrade completed — paru exited N. <count> repo packages + <count> AUR packages.

**Reboot required/not required** — <the concrete reason>

## Needs your attention
- **<finding>:** what it means, what it affects, the fix (and its tier).

## Fixed automatically
- <what was done, and why it was safe>

## Benign, ruled out
- <finding> — why it's not a problem (pre-existing since <date>, build noise, etc.)

## Verified clean
<one line: the checks that passed>
```

Attribute anything pre-existing with the evidence — a journal timestamp predating
the upgrade, or its presence in `baseline.txt`. "This was already failing on
Aug 19" is a fact the user can act on; "this might be unrelated" is not.

Finally: `upgrade.sh stop <workdir>` to close the tmux session. Leave the logs —
they're the record if something surfaces later.

---

## Reference files

- `references/triage.md` — every finding this workflow can surface: what it means,
  the fix, and its risk tier. Has a contents list; read the relevant section
  rather than the whole file.
- `references/known-quirks.md` — per-host behaviour (`Ollivanders` desktop,
  `shitblade` laptop), keyed by `hostname`. Read it in
  Phase 3 before concluding anything is broken.

## Adapting to other machines

The scripts are generic Arch + paru; only `known-quirks.md` is machine-specific.
On a different host, skip quirk checks whose hardware or config isn't present
rather than reporting their absence as a finding.
