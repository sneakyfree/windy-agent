- **Every outward tool call now writes one row in the agent's action ledger.** Sending an email, a text or a chat
  message, placing a call, uploading to Windy Cloud, publishing with the site builder and buying a domain went through
  the older tool list and left no ledger row; only the newer capabilities did. Each now writes the same row
  (`tool.<name>`, success or failure, how long it took), and so does each send the owner approves in their own words
  (a held email's "send", a first text's "yes", the builder's "yes, publish"). The row keeps the argument NAMES only,
  never their values, and no error text, so no recipient or message enters the ledger.
