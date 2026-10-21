# Changelog fragments

A PR never edits `CHANGELOG.md` itself. One line at the top of "## Unreleased" made every merge conflict with
the next PR's (10-21: five re-heads in one afternoon). Instead each PR adds ONE file here:

- name it `<pr-number>.md`, or `<branch-slug>.md` before the PR has a number (any unique `*.md` except this README);
- write the entry exactly as it should appear under "## Unreleased": one or more `- **Bold lead.** ...` bullets,
  in plain words for the owner;
- separate files never conflict, so a merge no longer re-heads the others.

`python3 scripts/changelog_fold.py` moves every fragment into `CHANGELOG.md` under "## Unreleased" (newest PR
first) and deletes the fragment files. `scripts/release.sh` runs it first; it can also run in its own PR.
`python3 scripts/changelog_fold.py --check` only validates the fragments (CI-safe, changes nothing).
