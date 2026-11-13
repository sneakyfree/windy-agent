### Changed
- `shell.exec`'s whole-host mode (`sandbox="host_rw"`) is now OFF unless the agent's soul config turns it on with
  `[capabilities.shell] host_rw = true`. Off, the tool does not offer it and refuses it even on the owner's turn, with
  a message that says how to turn it on. On, it works as before (owner band only). The Docker sandbox is unchanged.
