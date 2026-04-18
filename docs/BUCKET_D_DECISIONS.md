# Bucket D — Decisions needed

Per `docs/MERGE_TRIAGE.md`, the Wave 7 queue has **zero PRs in
Bucket D**. No PR is strictly blocked on a product decision from
Grant before merge; every PR either lands a self-contained fix or
(for Bucket C) needs a review skim rather than a branch point.

## If a Bucket D ticket were to arise later

The two latent decisions that could eventually become Bucket D
items (but don't today because the PRs leave doors open rather
than close them):

1. **`WINDYFLY_ENV=production` signal** — PR #8 makes an empty
   `DASHBOARD_PASSWORD` fail-closed when `WINDYFLY_ENV=production`.
   The PR accepts whatever value Grant picks as the production
   signal; it doesn't require a choice to merge. Decision becomes
   relevant only when the VPS deploy path actually ships.

2. **Production CloudWatch observability surface** — PR #11 strikes
   the phantom `windy observe` CLI command from the deploy doc and
   replaces it with an interim `amazon-cloudwatch-agent` recipe. A
   future PR could implement the CLI command natively; Grant would
   pick between (a) ship a native command, (b) keep the manual
   recipe, or (c) drop CloudWatch as a first-class story. None of
   those choices block any currently-open PR.

If either becomes load-bearing before launch, a Bucket D entry will
be added here and the corresponding PR will be retagged.
