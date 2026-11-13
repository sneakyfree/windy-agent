### Security
- `shell.exec` refuses any command that names the owner's credentials store (the lockbox: its folder, any copy, or its
  file), at every band and in both sandboxes, the owner included, before anything runs. It uses the same name
  matcher as the file and GitHub tools. The Docker
  sandbox also masks the store under any mounted root; new tests pin both, with controls.
