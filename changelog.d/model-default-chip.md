- **`/model default` works.** It drops this chat's own model pick, so the agent goes back to its default (when
  that default is "auto", Windy Mind picks the model), and says which applies from the next message. Before, the
  word "default" was looked up as a model name, refused, and nothing changed: the "default" model chip in the
  Windy Fly app did nothing.
