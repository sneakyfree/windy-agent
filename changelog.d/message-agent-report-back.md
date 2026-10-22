- **When the agent asks another of the owner's agents something, the answer comes back to the owner.** Before,
  `message_agent` posted in a separate room and the answer stayed there, so the owner never heard it (Grant's
  Build-131 note 8). Now an ask made on the owner's own turn in Windy Chat is tracked. Only the asked agent's
  messages sent after the ask count, joined until it goes quiet for 15 seconds (or a direct reply to the ask), and
  no extra turn is taken in that room. ONE report goes to the chat the owner asked from when everyone has
  answered or after 5 minutes, saying who didn't answer. The answers reach the model only as fenced data, the
  report turn has no tools, and the owner's stop cancels it. While an agent is still answering, a second question
  to it is not sent (the agent is told to ask again after), so an answer can never land in the wrong chat. Dark: on only with
  WINDY_REPORT_BACK=1 (and WINDY_TEAMS). Asks made from Telegram are not tracked yet. Agent text shown to the model
  also loses any run of three angle marks, spaced or look-alike, so it can't imitate a fence.
