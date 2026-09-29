# Key Decisions

## SoloHost remains read-only

**Decision:** Do not turn the Pi app into a privileged recovery agent.

**Why:** The app is valuable as a trustworthy observer. Giving it Docker/host/Commander control would greatly expand blast radius and make causal diagnosis less trustworthy.

## Evidence Bridge is separate

**Decision:** Host evidence is summarized by a separate read-only bridge.

**Why:** The SoloHost app should not mount broad Windows/host paths. The bridge exposes a narrow contract instead.

## Additive SQLite migrations

**Decision:** Evolve schema in place with additive migrations.

**Why:** Telemetry and incident history are the product's memory. Releases must preserve them.

## No fabricated history

**Decision:** Do not backfill root-cause/recovery fields unless the required evidence existed.

**Why:** A plausible story is not the same as evidence. Historical unknowns should remain unknown.

## Unlisted Pi listing

**Decision:** Keep the app Unlisted — link only for now.

**Why:** This is still an operator/development utility and should not be broadly discoverable until the owner intentionally changes release posture.

## Observe before act

**Decision:** v0.6 recommendations do not execute recovery.

**Why:** Recovery Intelligence should first prove that recommendations are reliable. v0.7 will learn from outcomes, still without automatic execution.

## v0.7 learning threshold

**Decision:** A future learned playbook must show sample count and evidence quality.

**Why:** One successful recovery is anecdote, not a reliable local policy. Sparse/conflicting data must stay visibly uncertain.
