# 0001 — Use only the TRTYRAP + TRTDXFAP products

**Status:** Accepted

## Context

USPTO's Open Data Portal offers many trademark products (assignments, TTAB
proceedings, image data, and the patent side entirely). The project's purpose
is an analysis-ready corpus of trademark applications and registrations in
their current state.

## Decision

Ingest **only** `TRTYRAP` (annual applications backfile) and `TRTDXFAP` (daily
applications). Nothing else — no assignments, no TTAB, no images, no patent
products.

## Consequences

- The full-text application XML gives marks, owners, classifications,
  goods/services, prosecution events, and the correspondent — sufficient for
  the analysis goals.
- No specimen images: the strongest "sold on a marketplace" evidence is
  image-level and therefore out of scope; this is a known, documented limit
  and a possible future expansion.
- The two products compose cleanly: the annual is a full snapshot, the dailies
  are the current-year tail (see [DATA_SOURCES](../DATA_SOURCES.md)).
