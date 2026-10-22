- **Windy Code tools appear only where the IDE is really running.** A socket file left behind by an IDE that has exited
  no longer counts: the agent checks that something answers on it at start (WINDYCODE_AGENT_SOCK still configures it).
