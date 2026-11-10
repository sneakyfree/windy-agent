- **The local fallback ends on a real answer, not on "Windy Mind is up".** When your agent falls back to the small
  local model, it used to check whether Windy Mind was reachable, and switch back if it was. Windy Mind can be
  reachable while your agent's own model is refused, for example when your own AI key has run out of credit. The
  agent then announced a recovery in its log, failed the very next call and fell back again, over and over. Now it
  asks Windy Mind for one tiny real answer with the model it was using. An answer ends the fallback; a refusal keeps
  it, and the log says why.
