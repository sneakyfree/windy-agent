- **Windy Search mode B names its Search address at startup (still off unless WINDY_SEARCH_EPT_AGENT=1).** With the
  switch on, the agent writes one line when it starts saying which Windy Search address its proofs are made for, and
  whether that is the default. A Search whose own public address differs counts each proof as bad, or refuses it
  once it enforces, so a wrong WINDY_SEARCH_BASE_URL shows up in that one line first.
