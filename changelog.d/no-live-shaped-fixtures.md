- Tests: the made-up vault fixture keys no longer look like live Stripe secret keys (Windy Git's public secret scan,
  2026-11-02). They read "fixture_..." now, and tests/test_no_live_shaped_fixtures.py fails if a live-shaped Stripe
  string ever lands in tests/ again.
