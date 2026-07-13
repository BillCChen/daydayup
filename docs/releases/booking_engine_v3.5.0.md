# Booking Engine v3.5.0

## Reliability changes

- Direct-fast and guided-fast now share a bounded wave scheduler for one- and two-hour goals.
- Each wave uses at most three exclusive prewarmed HTTP clients and prioritizes distinct target hours before additional courts.
- The scheduler covers the complete configured court pool while the launch window remains open.
- Final `reservationPlace` calls remain single-flight. Single-hour success blocks later final submissions; two-hour runs retain only adjacent eligible hours after the first success.
- Transient candidates can be retried at most twice. A deferred `操作过快` response uses the longer cooldown without blocking the current wave on an unavailable retry owner.
- Exact booking retries `reservationPlace` once for `操作过快` while preserving the same candidate.

## Observability changes

- Structured events include engine version, run ID, monotonic offset, wave, attempt, candidate, client slot, endpoint timing, outcome, and completion reason.
- Run summaries include per-endpoint count, p50, p90, maximum latency, normalized outcomes, failures, and completed-hour count.
- Exact-booking history includes stage, attempt, elapsed milliseconds, and normalized outcome.
- Concurrent jobs use microsecond-and-PID log filenames to avoid cross-process log collisions.
- Raw credentials, full successful payloads, and structured failure payloads are excluded from logs; query, cookie, form, and JSON credential forms are redacted.

## Default direct-fast settings

- `direct_max_inflight=3`
- `direct_max_attempts=2`
- `direct_spec_adjacent_delay=0`
- `reservation_place_gap=0.35`
- `reservation_place_fast_retry_gap=0.8`

## Compatibility and rollback

- The captured API order remains `canBook -> getOfferInfo -> getUseCardInfo -> reservationPlace`.
- No state-file schema migration is required.
- The rollback baseline is commit `a502ea62f217ddaff288d30f93a49db0ea8b2da6`.
