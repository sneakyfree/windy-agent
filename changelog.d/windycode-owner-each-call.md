- **Windy Code's socket owner is checked on every call, not only at start.** A socket another user puts at the
  shared path after this user's IDE exits is refused at the connect.
