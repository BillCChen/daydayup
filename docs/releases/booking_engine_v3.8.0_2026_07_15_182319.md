# Booking Engine v3.8.0

As of: 2026-07-15 18:45 CST

## Release scope

This release adds an opt-in two-account coordinator for manual two-hour `direct-fast` and `guided-fast` booking. Single-hour manual booking, exact booking, cancellation, and continuous scanning remain on the existing single-account path.

## Coordinator behavior

- One process owns two isolated account contexts, each with its own token, session, card, HTTP client, failure counters, and reservation pacing gate.
- The coordinator assigns two different account slots to one adjacent-hour pair. `pool_2` starts 0.35 seconds after `pool_1` without adding a user-adjustable parameter.
- An hour can hold only one final-submit lease across the pool. A confirmed or unknown hour anchors any subsequent neighbor attempt.
- A final-submit timeout is reconciled up to three times through the submitting account. Stable absence tombstones the hour for the run; a query failure marks it unknown. Neither state can be taken over by the other account.
- Explicit business failure releases the hour for another court. One confirmed hour is retained when its neighbor cannot be confirmed; no automatic cancellation is attempted.
- Account pacing remains independent, while an upstream `too_fast` response also raises the pool-wide submission cooldown.

## API and operations

- `POST /api/booking/start` accepts `account_mode=multi_pool` and exactly two distinct `user_keys`; the existing `user_key` contract remains compatible.
- `DAYDAYUP_MULTI_POOL_MODE` enforces `off`, `dry_run`, or `live` on the server and defaults to `off`. In `dry_run`, the server forces the booking engine's dry-run flag.
- The engine independently enforces the same runtime mode, so a direct child-process invocation cannot bypass an `off` or `dry_run` deployment.
- A multi-pool process holds exclusive access against other manual booking processes. The server atomically rejects concurrent starts that would otherwise create isolated lease ledgers.
- Both users must be enabled and have a token, a usable card, and enough balance for the most expensive candidate hour before a process starts. Preflight failure never downgrades to one account.
- History records the two participants and structured ownership for each attempted hour. Order queries and cancellation remain tied to the original account.

## Credential hardening

- Two account payloads cross the process boundary in one stdin JSON line; token, session, and card values do not enter argv or the command label.
- Username and password field variants are redacted. The token-exchange password field is cleared after both success and failure.
- `local/users.csv` and its temporary replacement file are forced to mode `0600`.
- Logs identify accounts only as `pool_1` and `pool_2`; stable credential fingerprints are not emitted.

## Verification

- Full Python regression: 124 tests passed.
- Multi-pool focused regression: 37 tests passed.
- Thread-barrier tests exercise same-hour lease contention and concurrent process starts; a simulated `guided-fast` collector verifies account attribution without network access.
- JavaScript syntax, Python AST parsing, whitespace validation, generic credential-pattern scan, and protected-file hash verification passed.
- Validation used only fake clients and dry-run behavior. No real reservation or cancellation was issued.
- Independent implementation review and two correction re-reviews found no remaining P0-P2 issue.

## Deployment gate

- Release tag: `v3.8-multi-pool`.
- Production is initially deployed with `DAYDAYUP_MULTI_POOL_MODE=dry_run`.
- Live enablement requires password rotation, second-account Web OAuth onboarding, and read-only verification of both account sessions, cards, balances, and order visibility.
- The feature reduces account-level throttling and session-failure concentration, but cannot eliminate public-IP, network, or upstream service failures.

## Deployment result

- GitHub release commit and `v3.8-multi-pool` tag target: `9850e07992e89627b3ac1c096bfe9c74270004ef`.
- Alibaba Cloud backup and dry-run deployment completed. Web and scan run engine `3.8.0`; only Web receives `DAYDAYUP_MULTI_POOL_MODE=dry_run`.
- The production container passed 37 focused tests and an isolated fake-credential dry-run with adjacent, distinct slot ownership and zero final-submit HTTP events.
- Live remains disabled until the disclosed password is rotated and the intended second account is onboarded and preflighted through the Web flow.
