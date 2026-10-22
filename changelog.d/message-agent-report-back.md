- **When the agent asks another of the owner's agents something, the answer comes back to the owner.** Before,
  `message_agent` posted in a separate room and the answer stayed there, so the owner never heard it (Grant's
  Build-131 note 8). Now an ask made on the owner's own turn in Windy Chat is tracked: the other agent's next
  message in that room is taken as the answer (no extra turn there), and ONE report goes to the chat the owner
  asked from when everyone has answered or after 5 minutes, saying who didn't answer. The answers reach the model
  only as fenced data, the report turn has no owner tools, and the owner's stop cancels it. On with WINDY_TEAMS;
  WINDY_REPORT_BACK=0 turns it off. Asks made from Telegram are not tracked yet.
