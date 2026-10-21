- **The weekly fire drill backs up first and stops itself at the first sign of database trouble.** Before it
  stops anything, it now needs a fresh encrypted cloud backup of the agent's memory: one from the last hour, or
  it takes one. With no backup, it fails plainly and touches nothing. After every step it checks the agent's
  logs for a database warning ("not a database", split, locked), and on the first one it stops, skipping the
  remaining steps. It can post its result as one line to a board file (WINDY_DRILL_BOARD_FILE). The 10-18 drill
  was the moment Zero's memory broke; these are Boss's conditions for running it weekly again.
