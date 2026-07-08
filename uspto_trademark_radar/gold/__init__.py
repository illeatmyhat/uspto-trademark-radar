"""Gold layer — derived analysis tables over the silver corpus.

- `mark_features` — per-filing feature columns keyed by serial_no (coined-mark
  score, class/basis flags, goods-list length). Publishable.
- `operation_profile` — per-address aggregate portfolio statistics. Local
  only: outputs live under data/gold/ (git-ignored) and are not part of the
  publish path.

See docs/adr/0008-publication-ethics.md for the published-vs-local rule.
"""
