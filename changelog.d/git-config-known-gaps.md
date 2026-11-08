- File tools: a content rule (Boss's ruling of 2026-11-08). Any file whose text has a git-only section header at the
  start of a line ([remote, [url, [http, [credential, [submodule) and a real credential is refused with the same
  words as a credentialed git config, whatever its name or place. That closes the reverse lookup of a repo config's
  includes. Placeholders (<token>, ${VAR}, YOUR_TOKEN, xxx) are not credentials. The check runs on the bytes each tool
  already read: read_file's content, grep's text (every file, before the pattern), and edit's text before old_string is
  looked at. move uses a streaming check. A placeholder counts only when every part that could hold the secret is one. Also: a hard link (more than one link) is checked by content, a file
  directly in a git folder is scanned whole however big, and includes are followed to git's own depth of 10. In the
  shell sandbox, a hard link to a credentialed config under the root is masked, and so is a repo config's nested
  include. Not covered: hooks, and anything else under .git that is not a config.
