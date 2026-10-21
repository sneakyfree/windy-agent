- **Changelog notes no longer collide.** Each change now adds its note as its own small file in `changelog.d/`,
  and `scripts/changelog_fold.py` gathers them into this file at release time. Before, every change added a line
  in the same spot here, so each merge forced the next waiting change to be redone.
