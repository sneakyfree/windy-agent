- **Windy Code's IDE can be reached with the agent's own key (built dark).** With `WINDY_CODE_BUS_EPT_AGENT=1`, the
  agent signs in to the IDE's agent bus with its own short-lived Windy Code token and a fresh signed proof on every
  connection. It uses the stored passport token only when the IDE says its side is off, and it stops with a plain
  reason when the IDE refuses the passport. Off by default; Windy Code's own side is due Mon 11-09.
