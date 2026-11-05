- **An agent can swap its own signing key without a passport token (off for now).** With `WINDY_AGENT_KEY_OPS=1`,
  `windy agent-key rotate` registers the new key with a proof signed by the old one, and the old key then retires
  itself (Eternitas key-ops v1). A key-only computer no longer needs a sign-in to rotate. A retire Eternitas refuses
  keeps the old key until the daily job finishes it.
