- **Your agent's do-not-text list no longer holds phone numbers.** When someone texts STOP to a number your agent
  texts from, your agent has to remember them so it never texts them again. It now keeps a scrambled fingerprint
  of their number instead of the number, and its activity log keeps only the last four digits. Numbers stored by
  earlier versions are converted the first time this version starts. The key that makes the fingerprints is a
  small private file beside your agent's memory. It travels inside your encrypted backup, so a restore keeps the
  list working. If that key is ever lost, your agent texts only you until the key is back, and tells you once why.
  A backup made before this version does not hold the key.
  A STOP that arrives in the meantime is still kept and still confirmed. Restoring an older backup, or undoing a
  restore, never removes anyone from the list.
