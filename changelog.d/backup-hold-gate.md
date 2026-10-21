- **A backup of a collapsed memory is held instead of pushing out the good ones.** If the agent's memory has under
  half the episodes it had at the last good backup (and that backup had at least 20), the scheduled backup is
  held. While it's held, the agent logs one error a day and sends the owner one notice a day, with counts only.
  The owner can accept the smaller memory with `/backup accept` in the agent's terminal; the next check then
  uploads it as the new normal. After a failed upload the agent waits 6 hours before trying again, not every
  hour. `/backup now` now backs up the agent's configured memory file. Before, it backed up the default file,
  which on Windy 0 is an idle file, and still reported success. If the memory-collapse email fails to send, it
  is tried again at the next start.
