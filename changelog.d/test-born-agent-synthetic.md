- **A test-born agent marks its own traffic as test traffic.** An agent whose passport number starts with ET26-TEST-
  now sends X-Windy-Synthetic: 1 to Windy services (never to third parties), the same as a probe with
  WINDY_SYNTHETIC=1, so Windy's counts never mistake it for a person. A real passport never matches.
