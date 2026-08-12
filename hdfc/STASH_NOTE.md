# Recoverable git stashes — TRUE contents

Written 2026-08-12. **Nothing here is lost.** Both stashes are valid, reachable commit
objects and can be restored at any time. This note exists because `stash@{0}`'s own
message **understates its untracked set by roughly 5×**, and a stash message cannot be
rewritten in place — so the accurate inventory lives here instead.

## `stash@{0}` — the one created by this HDFC work

Created on branch `docs/refresh-bank-eval-reports` immediately before the HDFC
final-shape prompt refactor, so that main could be checked out with a clean tree.

Its stored message reads:

> WIP snapshot before HDFC final-shape prompt refactor + schema enums (auto-stashed
> 2026-08-12): 18 modified + 14 untracked across hdfc/icici/sbi from prior session

**The "14 untracked" figure is wrong. The true contents are:**

| | count | measured by |
|---|---|---|
| modified tracked files | **18** (+2,370 / −747) | `git stash show --stat stash@{0}` |
| **untracked files** | **76** | `git ls-tree -r --name-only stash@{0}^3 \| wc -l` |

Where the error came from: the label counted the **top-level untracked entries** that
`git status --porcelain` displays, and `git status` collapses a wholly-untracked
directory into a single line (`hdfc/logs/`, `sbi/var/`, `.claude/`, …). Those directory
entries expand to 76 individual files. The stash itself always contained all 76 — only
the description was short.

Spans `hdfc/`, `icici/` and `sbi/`. Includes prior-session working files such as
`hdfc/audit_null_inflation.py`, `hdfc/measure_direction_damage.py`,
`hdfc/verify_glyph_full.py`, `hdfc/direction_damage.json`,
`hdfc/glyph_verification_full.json`, `hdfc/null_inflation_audit.json`,
`sbi/network_visual_audit.json`, plus `hdfc/logs/` and `sbi/var/` trees.

To inspect or restore:

```bash
git stash show --stat stash@{0}            # the 18 modified tracked files
git ls-tree -r --name-only stash@{0}^3     # all 76 untracked files
git stash apply stash@{0}                  # restore without dropping the stash
```

`stash@{0}^3` is the third parent of a stash commit, which is where git stores the
untracked payload captured by `-u`. It exists only for stashes created with `-u`.

## `stash@{1}` — pre-existing, NOT created by this work

> On `fix/icici-rollup-honest-transaction-metrics`:
> `superseded-icici-report-rewrite-437ins`

| | count |
|---|---|
| modified tracked files | **1** (+437 / −408) |
| untracked files | **0** |

A superseded ICICI report rewrite from an earlier session. Left untouched by the HDFC
work. It has no untracked payload, so `stash@{1}^3` does not exist.

## Scope note

Neither stash was applied or dropped during the HDFC work. The HDFC commits were made on
a clean `main` checked out after stashing, and touched only `hdfc/` files.
