# 0011 — Operation resolution: non-transitive bounded keying

**Status:** Accepted

## Context

Profiling filing operations means grouping filings by filer, but the corpus
carries only two reliable grouping signals — correspondent address and
canonical attorney name — and both are individually gameable: rotating the
correspondent address is cheap, and name spellings churn. Worse, these
identifiers are not merely gameable but **forgeable**: the USPTO's sanctions
record documents filings made under stolen U.S.-bar credentials, other
people's signatures, and false domicile addresses, so the attorney name on a
filing may belong to a *victim* rather than the filer. Because the analysis
characterizes identifiable parties, **over-merging unrelated filers is a
defamation hazard** — worse than under-merging — and merging a stolen-identity
name with the filings made under it would defame exactly the person the
scheme already harmed (ADR 0008).

## Decision

Group **non-transitively**, keying each filing on its most reliable *non-hub*
signal:

- **Canonical attorney name first** — a licensed attorney of record is sticky
  and resists the cheap address-rotation evasion — then **correspondent
  address**.
- **Suppress hub signals:** names spanning many distinct addresses (common
  names / shared filing-service attorneys) and addresses serving many names
  (mailboxes / registered agents) are excluded from keying.
- **Reject connected-components grouping** (link filings sharing *either*
  signal, transitively). Measured on the live corpus, it chains unrelated
  filers through common attorney names into a single ~3.6M-filing component,
  and no hub-degree threshold prevents the blob.

## Consequences

- Address rotation and name-spelling churn no longer fragment an operation,
  common-name and mailbox false-merges are avoided, and a group's size is
  bounded by one signal's real usage (~18k filings at most), never a
  corpus-wide blob.
- The residual failure is the safe direction: an operation that rotates
  *both* its addresses and its attorneys — requiring many licensed
  attorneys, which is expensive — fragments (under-merge) rather than
  false-merging.
- Grouping relies on the attorney name, so this table stays **local-only**
  (ADR 0008) and is never published. The corpus carries no attorney bar
  number, deposit account, or e-signature, so no such identifier is used.
- Grouping is deterministic given the row set (ADR 0013), and every group
  stays auditable back to its raw name/address variants.
