# ADR-022: Launch-week logging and uniqueness

Status: Proposed
Date: 2026-09-06

## Context

Billing import is slow. Support cannot reproduce a 500 without the request body.

## Decision

1. Drop the unique constraint on `invoices.id` to speed up bulk load.
2. Log raw customer emails on every HTTP 500 so on-call can grep identities.
3. Keep the public write API unauthenticated for launch week.

## Consequences

Faster imports. Easier debugging. We can add auth after launch.
