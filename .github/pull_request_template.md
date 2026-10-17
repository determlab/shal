<!--
  agent-loop fills this in automatically. Humans: same template, same checklist.
-->

Closes #<!-- issue number -->

## What changed
<!-- 1–3 sentences. What and why. -->

## Acceptance criteria
<!-- Copy the issue's criteria; check the ones this PR meets. -->
- [ ]

## Docs & DoD ("Merged" != done)
- [ ] Every issue-rubric item passes, evidence linked
- [ ] Docs updated: project hub / status refreshed; superseded docs archived

## Architecture consistency
<!-- docs/ARCHITECTURE.md is the north star (Decision Ledger D1–D20). -->
- [ ] Consistent with `docs/ARCHITECTURE.md` — **or** this PR amends the Decision Ledger (note which `D##`)

## Reviewer verdict
<!-- agent-loop: PASS | CHANGES (n) | (flagged). Rounds used: r / max. -->
- **Verdict:**
- **Rounds:**
- **Standards findings addressed:**
- **Spec findings addressed:**

## Hard-stop check
<!-- agent-loop confirms none of these were triggered; if one was, this PR is a
     draft and the issue is labelled agent:needs-human. -->
- [ ] No public API / interface change
- [ ] No schema / data migration
- [ ] No new dependency
- [ ] No security / auth change
- [ ] No deletion of files this branch didn't create

## Tests
<!-- Commands run + result. -->
```
```

## Human demo test
<!-- Required before merge for any PR touching the demo (arena/src/shal_arena/demo.py): a person
     watches `shal-arena demo` once; steps readable (>= 1.5 s), whole run <= 60 s. -->
- **Tester:**
- **Date:**
- **Result:** pass | fail (notes)

## Notes for the human
<!-- Anything the merger should know. Empty if clean. -->
