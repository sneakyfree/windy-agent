- File tools: read_file, grep, edit, move and the other path tools refuse a git config that carries a credential (a
  token or password in a URL, or an http extraheader), by the same check the shell sandbox masks with
  (capabilities/git_secrets.py, #761). Covered: repo, bare, submodule and worktree configs, a config renamed and
  linked, user-level configs (.gitconfig and its variants, ~/.config/git/config) and the files the global configs
  include (one level). The shell sandbox also masks the files a repo config includes when they sit under the mounted
  root. Known gap: the file tools don't recognise a file that a repo's config includes, and includes of includes go
  unchecked everywhere.
