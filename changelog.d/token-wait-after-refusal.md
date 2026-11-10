- **Your agent waits when Eternitas says "too many requests".** Each time your agent calls a Windy service it shows a
  short-lived pass from Eternitas. When Eternitas refused to issue one for being asked too often, the agent asked
  again on the very next call, and every refused ask kept the refusal going. One agent stayed locked out for days
  this way. Now, after such a refusal, the agent does not ask again until the wait Eternitas names is over (60
  seconds if it names none). A pass that is still valid keeps working in the meantime.
