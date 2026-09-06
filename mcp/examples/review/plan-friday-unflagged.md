# Change plan: Friday schema + email logs

## Goal

Ship the billing hotfix this week.

## Steps

1. Apply expand-contract DDL on Friday 22:00 with no feature flag.
2. Log full customer emails on every 500 until the bug is found.
3. Skip the restore drill this launch week to save time.
