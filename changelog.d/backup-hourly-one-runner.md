- **The agent's cloud backup really runs daily now, and only once.** Whether a backup is due is now checked every
  hour against the 24-hour interval. A restart no longer pushes the next backup back by a day: on Windy 0 the
  gaps had grown to 34-48 hours. When an agent runs both Telegram and Matrix, only the one process that runs
  maintenance uploads. Before, both uploaded the same backup, so Cloud's 5 slots held duplicates.
