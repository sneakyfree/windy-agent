- **If the agent's memory file can't be read at startup, the owner hears about it right away.** The agent still
  sets the damaged file aside and starts fresh, so it can run. Now, at that same moment, it also leaves a small
  note (file names and sizes only). The first process to start the agent's routine work then reports it once: an
  error line in the log, one email to the owner, and one line on the operators' board when that is configured.
  On Windy 0 this happened on 10-19, and nobody noticed for two days.
