- Shell sandbox: a repo's git config (and each submodule's) that carries a credential (a token or password in a remote
  URL, or an http extraheader) is masked with the empty read-only file. A plain ssh or https remote stays readable.
  The mask walk still never enters .git (Hub's note on #755).
