# Booking Engine v3.7.0

As of: 2026-07-15 14:44 CST

## Purpose

Engine 3.7.0 repairs the timeout-reconciliation and rate-limit behavior observed in the 2026-07-15 production booking run. It is designed to work with the Web console defaults and does not require operator tuning.

## Booking behavior

- A successful final submit now enforces a 1.8-second cooldown before the next `reservationPlace` request.
- The first `操作过快` result immediately raises the 1.2-second adaptive base to 1.8 seconds; subsequent consecutive results use 2.7 seconds and then the 3.0-second cap.
- A timed-out `reservationPlace` request is never replayed automatically.
- Reconciliation queries recent orders without server-side date filters and matches the target date, start/end hour, and court locally.
- Read-only reconciliation snapshots are scheduled 0.25, 1.0, and 2.5 seconds after the submit timeout begins reconciliation.
- A matching order in any snapshot confirms success.
- Any failed or incomplete snapshot leaves the outcome indeterminate and stops further final-submit writes.
- Only three successful snapshots with no target match classify the request as stably absent; the scheduler may then continue with other candidates but not the timed-out candidate.

## Logging

Structured events now record:

- the complete reconciliation schedule;
- each snapshot number, scheduled delay, HTTP status, query time, order count, and remaining booking-window budget;
- the final confirmed, stable-not-found, query-failed, or deadline-expired decision;
- whether recovery continues with other candidates;
- the 1.8-second success cooldown and reconciliation schedule in every run configuration.

## Local verification

- `python -m unittest discover -s tests -v`: 87 tests passed.
- `node --check web/app.js`: passed with Node.js 26.0.0.
- Python AST parse: passed with Python 3.12.11 from the project environment.
- `git diff --check`: passed.
- Protected skip-worktree files retained their pre-change SHA-1 values.

## Deployment status

Production deployment and read-only runtime verification are pending.
