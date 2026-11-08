- A background goal now pauses and asks the owner before any destructive step (delete, move, git commit, or a file
  overwrite, judged per call), not only before outward ones (Boss BOARD 2026-11-07T19:39:14Z). The TRUSTED-band guard
  test now checks by ast that goal_band() holds the one allowed TRUSTED return (Hub's note on #757).
- fs.write_file: only a real boolean `overwrite: true` replaces an existing file, and any other overwrite value
  ("true", 1, "yes") still raises the call to WRITE_DESTRUCTIVE (TRUSTED+), so it is refused at USER band and pauses
  a goal. Before, such a value overwrote the file at USER band (the args schema is not enforced; Hub on #759).
