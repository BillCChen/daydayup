# Booking Reliability v3.5 Task Plan

## Goal

Release and deploy a versioned booking-engine update that improves the probability of winning at least one hour, honors the configured attempt window, uses the configured court pool, and emits redacted stage-level timing evidence for further optimization.

## Current Phase

Phase 5

## Confirmed Baseline

- Local `main`: `a502ea62f217ddaff288d30f93a49db0ea8b2da6`.
- GitHub `origin/main`: `a502ea62f217ddaff288d30f93a49db0ea8b2da6`.
- Aliyun `/opt/huairou/daydayup/repo`: `a502ea62f217ddaff288d30f93a49db0ea8b2da6`.
- Local and Aliyun working trees were clean before implementation.
- Production release-time evidence shows seven `canBook` successes and zero `reservationPlace` successes across the two runs on the current gate implementation.

## Candidate Routes

### Route A: Parameter-only tuning

- Conditions: keep the current speculative structure; widen `per_hour_limit` and shorten reservation gaps.
- Strengths: smallest patch and easiest rollback.
- Costs: leaves one-shot execution, per-worker cold TLS connections, weak single-hour behavior, and misleading window semantics.
- Failure mode: either remains too slow or reintroduces request bursts and `操作过快`.
- Decision: rejected as the main route; individual parameter changes may be retained only when supported by tests.

### Route B: Bounded wave scheduler with first-hour priority

- Conditions: preserve the captured four-call API contract while changing candidate scheduling and observability.
- Strengths: can use the whole configured court pool, honor the window, reuse prewarmed clients, prioritize any one-hour win, and keep final submissions bounded.
- Costs: more state and concurrency coordination; must prove no duplicate same-slot final submissions.
- Failure mode: an overly aggressive wave still triggers rate limits; an overly conservative final gate loses the race.
- Decision: selected main route.

### Route C: Availability-guided selection before every attempt

- Conditions: use `get_places` snapshots to rank only visible openings.
- Strengths: fewer blind requests and lower rate-limit pressure.
- Costs: adds a query before the critical path and the snapshot may be stale by final submission.
- Failure mode: competitors take the slot between snapshot and commit.
- Decision: retained as a secondary/fallback signal, not the release-time primary route.

### Route D: Skip captured prerequisite calls

- Conditions: call `reservationPlace` without one or more of `canBook`, `getOfferInfo`, or `getUseCardInfo`.
- Strengths: theoretically shortest path.
- Costs: violates the verified request sequence without server-side evidence that skipping is safe.
- Failure mode: rejected payload, inconsistent booking, account-side risk.
- Decision: rejected.

## Phases

### Phase 1: Baseline and evidence

- [x] Verify local, GitHub, and Aliyun commit alignment.
- [x] Confirm clean working trees.
- [x] Preserve production failure metrics and exact timing evidence.
- **Status:** complete

### Phase 2: Specification and design

- [x] Define scheduler invariants and concurrency bounds.
- [x] Define redacted structured log events and summary metrics.
- [x] Define version/tag and rollback contract.
- **Status:** complete

### Phase 3: Implementation

- [x] Implement the chosen scheduler for single- and two-hour direct-fast flows.
- [x] Honor the configured window and full court pool through bounded waves.
- [x] Reuse/prewarm critical-path clients without sharing one HTTP connection concurrently.
- [x] Add detailed redacted timing, candidate, wave, gate, and outcome logs.
- [x] Correct history finalization observability if it overlaps the change safely.
- **Status:** complete

### Phase 4: Verification and review

- [x] Add deterministic unit tests for single-hour, all-court waves, window behavior, gate behavior, logging, and redaction.
- [x] Run the complete unit suite without persistent production-log side effects.
- [x] Run syntax checks and a separate review checklist.
- [x] Verify the working tree contains no unrelated changes or generated fragments.
- **Status:** complete

### Phase 5: Version, publish, and deploy

- [ ] Commit the verified scope and create a v3.5 release tag.
- [ ] Push the exact commit and tag to GitHub.
- [ ] Back up Aliyun state and deployment metadata without copying secrets into Git.
- [ ] Fast-forward the Aliyun repo to the exact pushed commit and rebuild/restart only Daydayup services.
- [ ] Verify code identity, container status, Web HTTP path, scan worker, and redacted logs.
- [ ] Confirm rollback command points to `a502ea6` and the previous image/build path remains recoverable.
- **Status:** in_progress

## Acceptance Criteria

1. A single-hour direct-fast run can schedule more than one candidate through a bounded wave instead of serially exhausting full four-call chains.
2. A two-hour run attempts additional configured courts when the first three per hour fail.
3. `window_seconds` bounds repeated waves rather than being bypassed after the first failed speculative batch.
4. No candidate submits `reservationPlace` concurrently with itself; global final-submit concurrency and minimum spacing are explicit and tested.
5. Logs contain version, run ID, monotonic offsets, phase, wave, candidate identity, endpoint timing, queue/gate wait, categorized outcome, and aggregate percentiles/counts.
6. Logs never print token, JSESSIONID, raw card index, offer ID, full card/member details, or full upstream payloads.
7. All unit tests pass; no real booking or cancellation is triggered during verification.
8. Local, GitHub, and Aliyun all end on the same new commit.

## Key Risks

- The upstream anti-rate-limit rule is not visible; concurrency values require later real release-window evidence.
- `canBook` success may not reserve a slot, so even improved scheduling cannot guarantee success under extreme contention.
- Reordering or skipping the captured API sequence is out of scope without a separate controlled experiment.
- Production deployment must preserve `/opt/huairou/daydayup/state/local` and `/opt/huairou/daydayup/state/logs`.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Diagnostic test execution created four fixture log files in the production log mount | 1 | Deleted only the `20260713_1143` fixture files and reverified 16 original production booking logs plus clean Git state |
| A shell inspection loop used zsh's reserved `path` array as its loop variable and temporarily cleared command lookup inside that subprocess | 1 | Reran with `item` and absolute command paths; no filesystem change occurred |

## Review Boundary

No independent agent review is available in this task. A separate self-review pass will inspect the diff against the invariants above, followed by deterministic tests and a production smoke check that does not book or cancel.
