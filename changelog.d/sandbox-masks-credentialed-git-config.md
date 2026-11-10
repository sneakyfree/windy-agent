- Shell sandbox: every git config file that carries a credential (a token or password in a URL, or an http
  extraheader) is masked with the empty read-only file: a repo's .git/config and config.worktree, a bare or mirror
  clone's config, each submodule's (one level) and each linked worktree's. A symlinked one is masked as a link, and its
  target too when that is under the mounted root. Plain remotes stay readable. The check lives in ONE module
  (capabilities/git_secrets.py) for the sandbox and, next, the file tools (Hub on #755 / #761).
