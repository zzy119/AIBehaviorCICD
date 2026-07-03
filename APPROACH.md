# Approach

## What I Built

I built an AI Behavior Release Manager: a small production-minded workflow for shipping AI behavior changes safely.

The app supports the core lifecycle:

```text
Create behavior version
-> run eval comparison against production
-> classify improvements and regressions
-> review severity-aware release gate
-> run local A/B simulation for an early launchability signal
-> submit to QA / A-B test environment
-> canary to 10%, ramp to 50%, then promote to production
-> rollback, reject, or inspect feedback signals and audit history
```

The seeded demo includes a production behavior, a risky candidate that improves tone but regresses refund policy, and a safer candidate that preserves policy guardrails.

## Product Thesis

AI behavior changes should be released like production code. A prompt diff is not enough. Teams need versioning, apples-to-apples evals, regression detection, release gates, feedback signals, and audit history.

The MVP intentionally focuses on a polished vertical slice rather than a broad prompt playground.

## Key Decisions

### Deterministic by Default

The app runs without external API keys. It uses a deterministic mock model and deterministic evaluator so reviewers can reproduce the same release decision in a fresh Linux container.

Real LLM calls would be an enhancement, not a requirement. For a release gate, reproducibility matters more than flash.

### Compare Against Production

The important question is not "is this prompt good?" It is "did this candidate make behavior worse than what users already have?"

Every eval run compares candidate and production over the same eval suite under the same provider snapshot, evaluator version, and run configuration.

### Hard Gates Beat Aggregate Scores

The release score is useful context, but it cannot hide critical failures. A candidate can improve tone and still be blocked by one critical refund or safety regression.

Hard overrides include:

- critical regression blocks
- candidate pass rate below production blocks
- stale eval blocks
- partial/non-comparable eval blocks

### Feedback Is a Signal, Not Truth

Feedback is weighted and configurable. The app also applies a mixed-signal penalty when high-risk negative signals worsen even if aggregate sentiment improves.

This models a common production trap: better thumbs-up rate can hide worse escalation or policy-confusion outcomes.

The MVP stores both aggregate feedback signals and concrete prompt feedback events. Aggregate signals drive the release gate, while prompt feedback events preserve the evidence a prompt engineer would use for the next candidate: source, environment, rollout stage, traffic exposure, user scenario, feedback text, and suggested prompt change.

In production, those events would be ingested from QA review, internal dogfood, 10% canary, 50% ramp, and full production telemetry. The system should not auto-edit production prompts from feedback. It should create evidence for the next candidate, then send that candidate through the same release pipeline.

The MVP separates source trust:

- production rollout feedback gets full weight because it comes from real exposure
- local proxy feedback from historical replay, ML classifiers, or reviewers gets lower weight
- local feedback is promoted when it contains red flags on high-risk areas such as refund policy, safety, or escalation

This keeps local feedback useful without pretending it is as reliable as production telemetry.

### Local Evidence Index

The feature/use-case/release-evidence mapping is stored locally in SQLite as derived data. It makes prior release evidence easy to revisit without introducing Redis, MCP, or another service.

The raw eval runs, feedback signals, and audit events remain the source of truth.

### Progressive Rollout Over One-Click Promote

The UI shows the release as a pipeline: Candidate Pool, Local A/B Simulation, QA / A-B Test, Prod Canary 10%, Prod Ramp 50%, and Production.

Local A/B runs before QA because it is a cheap early signal: if the candidate cannot beat production in a controlled apples-to-apples comparison, it should not consume rollout attention. QA / A-B then validates the candidate in a controlled test environment while still comparing against a production baseline or holdout-style replay before real user exposure.

Production rollout is gradual because offline and QA/A-B checks never perfectly predict production. The MVP models guardrails at 10%, 50%, and 100% rollout so reviewers can see the intended operating model: compare candidate telemetry against the baseline and holdout group, then ramp or rollback.

### Approved Prompt Registry

Successful production releases update an approved prompt registry keyed by use case. This acts like a local cache for fast future lookup: "for this use case, which prompt/model behavior is currently approved?"

It is not a bypass around the release gate. If a future candidate changes behavior, model, provider snapshot, or eval suite, it still needs to rerun the pipeline. The registry is derived release evidence, not the source of truth.

## What I Left Out

- real auth/RBAC
- live traffic routing
- real analytics ingestion
- background eval workers
- broad LLM provider abstraction
- collaboration comments

Those are valid production features, but they do not prove the core release-management loop in a one-day MVP.

## Failure Paths

The app explicitly handles more than the happy path:

- candidate cannot promote before eval
- candidate edit makes prior eval stale
- blocked gate prevents promotion
- partial evals cannot promote
- provider snapshot drift makes comparisons invalid
- failed rollout guardrails can rollback from QA / A-B or production ramp stages
- evidence cache is derived and rebuildable
- promotion is transactional so only one production version exists

The first production risk is not scale. It is promoting on stale, partial, or misleading evidence.

## What Breaks First

### Eval Quality

Deterministic phrase checks are reliable and explainable, but they miss nuance. The next step would be calibrated LLM judging plus human-reviewed golden labels.

### Feedback Bias

Seeded feedback demonstrates the decision model, but real feedback is noisy and segment-dependent. A production version should normalize by traffic source, user cohort, and interaction type.

### Synchronous Evals

The MVP runs evals synchronously. Larger suites need background jobs, retries, cancellation, and progress reporting.

### Governance

There is no auth or approval policy. A production system needs RBAC, required reviewers, staged rollout rules, and environment-specific permissions.

### Risk Classification

The MVP treats risk as a gate output, not as a separate ML classifier. In production, I would add a risk score based on use-case criticality, prompt/config diff size, production relevance, historical incident rate, and traffic exposure.

High relevance to existing production behavior reduces uncertainty, but it does not automatically mean low risk. A small prompt change in a refund, safety, or escalation workflow can still deserve a slower rollout.

## What I Would Build Next

- background eval runner
- eval case editor with review history
- prompt diff and config diff view
- real provider plugin behind optional env keys
- real traffic router for staged rollout
- automated rollback from production telemetry guardrails
- production feedback ingestion
- project/team boundaries and approval policies
