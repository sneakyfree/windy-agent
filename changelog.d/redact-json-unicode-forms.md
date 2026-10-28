- `windyfly.vault.redact` also masks a value's JSON form with non-ASCII left as-is (what `JSON.stringify` writes) and
  with upper-case `\uXXXX` escapes; before, a server echoing the key that way got a piece of it past the mask. The
  redactor has no caller on master yet (only the dark vault.call_api, #665, will use it).
