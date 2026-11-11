- **The runaway-spend guard now works on Windy Chat too.** If your agent starts spending much faster than normal
  (by default more than $5 an hour, measured over 15 minutes), it pauses itself and tells you in your direct room.
  Until now this guard ran only for agents on Telegram; an agent on Windy Chat alone had none. `/resume` starts it
  again, `/spend` shows the breakdown, and `WINDY_BURN_AUTOPAUSE_USD_PER_HOUR=0` still turns the guard off.
