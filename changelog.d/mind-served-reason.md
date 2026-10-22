- **The agent no longer makes up why a different model answered.** When Windy Mind serves a reply with another
  model than the one picked, it now says why (`windy_served`, menu.v1 1.13.0). The agent's runtime facts carry
  Mind's one line, built only from Mind's own template and words, and only for the most recent reply. A standing
  rule says only that line may give the reason; without one, the agent says it doesn't know. Before, Grant picked
  Haiku, another model answered, and the agent invented a reason (Build-131 note 1). Nothing changes until
  windy-mind #490 is live.
