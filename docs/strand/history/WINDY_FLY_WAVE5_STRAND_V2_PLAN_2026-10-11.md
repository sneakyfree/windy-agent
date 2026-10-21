# Windy Fly Wave 5: strand v2 as an executable scorecard (plan for Hub, before code)
_Windy Agent, 2026-10-11 14:39Z (clock-checked). Part of the Grant-approved strand-to-green plan (Wave 5: "Rewrite the strand (retired codons marked with decisions; new genes ...). Every codon = a CI test under tests/strand/ named for it.")._

## Size (from the 10-10 audit, ~/scratch/dna-gap/p1-p5.md, master 500aa13)
~680 codon rows: Part 1 167, Part 2 104, Part 3 127, Part 4 114, Part 5 170. At 500aa13: 365 green, 188 yellow, 60 red, 26 retired. Waves 1-4 + the 10 small fixes + Wave 3 have since retired or fixed many of the reds; each part is re-graded at its PR.

## Shape
- **One source of truth:** `docs/strand/STRAND_V2.md`, one table per gene, one row per codon: `codon id | what it promises (1 line) | status | test`.
  - status = `live`, `retired (<decision, PR>)` or `planned (<owner/gate>)`.
  - test = a pytest node id under `tests/strand/` (or an existing test file when one already pins the codon exactly).
  - New genes get their own sections: Windy Mind, Eternitas passports, teams/pair rooms, Mail, Calendar, Cloud, phone tools, Vault.
- **The scorecard is executable:** `tests/strand/test_scorecard.py` parses STRAND_V2.md and fails if:
  - a `live` codon has no test, or names a test pytest cannot collect;
  - a test under tests/strand/ is not named by any codon;
  - a `retired` row has no decision/PR.

  CI already runs `pytest tests/`, so tests/strand/ is in CI with no workflow change.
- **The old Parts 1-5 stay** as history, with one banner line pointing to v2. They are not rewritten in place.

## PRs (one at a time, Hub reviews each)
- **5a frame:** STRAND_V2.md skeleton + scorecard meta-test + tests/strand/ package + the ARCHITECTURE.md fixes (line 494 `sub_agents.py # legacy depth-1` -> removed, plus any other line naming a retired file) + banners on Parts 1-5. Codon rows for Part 1 only, to prove the format.
- **5b-5f:** one per old part, in order: codon rows + their tests. Anything still red becomes a small fix PR (or `planned` with an owner) instead of hiding in a test.
- **5g:** the new genes (Mind, Eternitas, teams, Mail, Calendar, Cloud, phone, Vault). Mostly pointers to the existing contract/feature tests; new tests only where a promise has none.
- Then the re-audit: rebuild the audit page from the scorecard and publish it to the same artifact.

## Rules
- A codon test asserts the PROMISE (behaviour), not the implementation line.
- Tests must be mutation-checkable: no tautologies; each new codon test is checked to fail with the codon broken.
- No network, empty HOME.
- Retirements cite the PR that retired them (#499-#505, #522, ...).

## Question for Hub
OK with the shape (one doc as the scorecard + a meta-test, old parts kept as history) and the PR order?
