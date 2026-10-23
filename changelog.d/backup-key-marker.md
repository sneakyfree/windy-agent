- **A cloud backup says which key sealed it.** Its metadata now carries `key: "owner"`. Since #573 only an owner-held
  WINDY_BACKUP_KEY uploads, so Windy Cloud can tell these from older archives that may have used a passport-derived key.
