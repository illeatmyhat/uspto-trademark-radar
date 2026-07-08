# 0007 — Latest-state-only; no version archaeology

**Status:** Accepted

## Context

A case file is a living record whose fields change over time (status, owner,
dates). The pipeline could retain every historical version of every field, or
only each case's current state.

## Decision

Retain **latest state only.** Superseded field values are not kept. The dataset
reflects each case's most recent known state (annual baseline + daily replay,
latest-wins on `serial_no`).

## Consequences

- Simpler schema and build; one row per case file.
- A single `data_current_through` date describes the whole dataset.
- Consumers who want version history rebuild it themselves from the USPTO
  dailies (available on the portal and in the bronze mirror,
  [ADR 0005](0005-bronze-mirror.md)).
- Documented as a known limitation in the dataset card.
