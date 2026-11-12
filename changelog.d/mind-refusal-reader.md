- **A refused provider key is recognised however Windy Mind reports it.** When your own provider key is refused
  (out of credit, or the key itself), your agent tells you which key to fix and does not keep retrying. That message
  depended on an answer form that never arrived whole through Mind's public address. The agent now reads the new
  form Mind is moving to (a 4xx answer that names the connection) as well as the old one, so it works whichever
  side updates first.
- **Gentler checks while your agent is on its local fallback.** It still asks Mind every 2 minutes whether it can
  answer again; after 15 failed checks in a row it asks every 10 minutes, and when Mind says when to come back
  (Retry-After on a busy or rate-limit answer, up to 1 hour) it waits until then. A new fallback starts again at 2
  minutes.
- **The "back from lifeboat" message says which model answered.** If Mind reports that it served a different model
  than the one your agent uses, the message says so ("with X instead of Y for now") instead of "healthy again".
- **One log line for every non-success answer from Mind**: the status, whether Retry-After came, the names of
  Mind's headers and the size of the body. Never a header value and never the content.
