- **Windy Search can be reached with the agent's own key (built dark).** With `WINDY_SEARCH_EPT_AGENT=1`, web search
  and page fetches send the agent's own short-lived Windy Search token plus a fresh signed proof on every request,
  instead of the stored passport token. They fall back to the stored token only when Windy Search says its side is
  off (or, for now, when a page render isn't available on the new path). Off by default; nothing changes until Windy
  Search's own switch is on and the flag is set.
