- `windyfly.vault.redact` now NORMALIZES instead of only listing forms (Boss 10-28): scrub() also decodes the text
  through JSON string escapes (any case, surrogate pairs), percent-encoding (any case, UTF-8) and HTML character
  references, one layer and every ordered pair of two, and masks the exact source span of every whole form it finds
  there. Single linear passes (1 MiB of hostile input scrubs in well under a second). A seeded property test throws
  random one- and two-layer escapings of five awkward values at scrub() (always masked whole) and of near misses
  (never masked).
