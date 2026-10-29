- **Owner confirmation for email to a NEW recipient is ON by default** (Boss 2026-10-29, principle 10: nothing
  reaches a new person without the owner's yes). `WINDY_SEND_CONFIRM` now means on unless it is exactly `0` (a lab
  switch); unset, empty or anything else is on. Recipients the owner has already approved still go straight out.
  With no mailbox, send_email now says so at once instead of holding a draft nothing could send.
