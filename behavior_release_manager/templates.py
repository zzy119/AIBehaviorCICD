from html import escape

from .utils import percent


RELEASE_MAP_STAGES = [
    ("1", "Candidate Pool", "Draft prompt/model config enters review.", ["Candidate Pool"]),
    ("2", "Eval Gate", "Apples-to-apples comparison against production.", ["Local A/B Simulation", "Needs Tuning", "Blocked"]),
    ("3", "Local A/B", "Cheap early launchability signal before QA.", ["Local A/B Simulation"]),
    ("4", "QA / A-B", "Controlled QA replay with holdout-style A/B comparison.", ["QA / A-B Test"]),
    ("5", "10% Canary", "Small production exposure with guardrails.", ["Prod Canary 10%"]),
    ("6", "50% Ramp", "Broader rollout only if healthy.", ["Prod Ramp 50%"]),
    ("7", "Production", "Approved behavior becomes default.", ["Production"]),
]


DECISION_LANES = [
    (
        "Needs Tuning",
        "Needs Tuning",
        "warn",
        "Good candidate, but keep it in review with specific path-to-green advice.",
    ),
    (
        "Blocked",
        "Block",
        "block",
        "Hard regression, stale eval, or unhealthy rollout guardrail. If no action is taken within 1 day, this candidate should auto-degrade to Rejected.",
    ),
    ("Rejected", "Rejected", "rejected", "Human decision to stop this candidate."),
]


PRIMARY_PIPELINE_STAGES = [
    "Candidate Pool",
    "Local A/B Simulation",
    "QA / A-B Test",
    "Prod Canary 10%",
    "Prod Ramp 50%",
    "Production",
]


DECISION_STAGE_NAMES = ["Needs Tuning", "Blocked", "Rejected"]
PIPELINE_STAGE_LABELS = {"Blocked": "Block"}
PIPELINE_STAGE_KINDS = {
    "Needs Tuning": "needs-tuning",
    "Blocked": "blocked",
    "Rejected": "rejected",
}


def h(value):
    return escape(str(value if value is not None else ""))


def fmt_pct(value):
    return "{}%".format(percent(value))


def badge(text, kind="neutral"):
    return '<span class="badge badge-{}">{}</span>'.format(kind, h(text))


def gate_badge(state):
    return badge(state.upper(), state)


def gate_kind(state):
    return {
        "pass": "pass",
        "warn": "warn",
        "block": "block",
        "baseline": "neutral",
        "rejected": "bad",
    }.get(state, "neutral")


def display_version_name(version):
    name = version["name"]
    if version["status"] != "production" and name.startswith("Production - "):
        return "Prior " + name
    return name


def metric_card(label, value, detail="", kind="neutral"):
    return """
    <div class="metric metric-{}">
      <div class="metric-label">{}</div>
      <div class="metric-value">{}</div>
      <div class="metric-detail">{}</div>
    </div>
    """.format(
        h(kind), h(label), h(value), h(detail)
    )


def layout(title, body, message=""):
    notice = ""
    if message:
        notice = '<div class="notice">{}</div>'.format(h(message))
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{}</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <header class="topbar">
    <div>
      <div class="eyebrow">AI Behavior Release Manager</div>
      <h1>Ship behavior changes with release discipline</h1>
    </div>
    <nav>
      <a href="/">Dashboard</a>
      <a href="/cases">Eval Suite</a>
      <form method="post" action="/demo/reset">
        <button type="submit">Reset Demo</button>
      </form>
    </nav>
  </header>
  <main>
    {}
    {}
  </main>
</body>
</html>""".format(
        h(title), notice, body
    )


def version_select(versions, candidate):
    options = []
    for version in versions:
        if version["status"] == "production":
            continue
        selected = " selected" if candidate and version["id"] == candidate["id"] else ""
        options.append(
            '<option value="{}"{}>{} ({})</option>'.format(
                h(version["id"]), selected, h(version["name"]), h(version["status"])
            )
        )
    return """
    <form method="get" action="/" class="inline-form">
      <label>Candidate</label>
      <select name="candidate" onchange="this.form.submit()">{}</select>
    </form>
    """.format(
        "".join(options)
    )


def render_dashboard(state, message=""):
    production = state["production"]
    candidate = state["candidate"]
    eval_run = state["eval_run"]
    gate = eval_run["gate"] if eval_run else None
    summary = eval_run["summary"] if eval_run else None
    feedback = state["feedback_score"]

    if eval_run:
        gate_state = gate["state"]
        gate_detail = "Release score {}".format(gate["release_score"])
    else:
        gate_state = "not evaluated"
        gate_detail = "Run an eval before promotion"

    hero = """
    <section class="hero">
      <div>
        <div class="eyebrow">Current production</div>
        <h2>{}</h2>
        <p>{}</p>
      </div>
      <div class="hero-actions">
        {}
        <form method="post" action="/eval/run">
          <input type="hidden" name="candidate_id" value="{}">
          <button class="primary" type="submit">Run Eval Comparison</button>
        </form>
      </div>
    </section>
    """.format(
        h(production["name"]),
        h(production["release_notes"]),
        version_select(state["versions"], candidate),
        h(candidate["id"]),
    )

    metrics = """
    <section class="metrics-grid">
      {}
      {}
      {}
      {}
    </section>
    """.format(
        metric_card(
            "Gate",
            gate_state.upper(),
            gate_detail + (" - stale eval" if state["stale"] and eval_run else ""),
            gate_kind(gate_state),
        ),
        metric_card(
            "Candidate pass rate",
            fmt_pct(summary["candidate_pass_rate"]) if summary else "n/a",
            "Production {}".format(fmt_pct(summary["production_pass_rate"])) if summary else "",
        ),
        metric_card(
            "Regressions",
            summary["regression_count"] if summary else "n/a",
            "{} critical".format(summary["critical_regression_count"]) if summary else "",
        ),
        metric_card(
            "Feedback delta",
            feedback["adjusted_feedback_score"] if feedback else "n/a",
            "Penalty {}".format(feedback["mixed_signal_penalty"]) if feedback else "",
        ),
    )

    body = hero + metrics
    body += render_pipeline(state["pipeline"])
    body += render_versions(production, candidate, state["behavior_preview"])
    body += render_eval_results(eval_run)
    body += render_evidence(state["evidence"])
    body += render_feedback(state)
    body += render_audit(state["audit_events"])
    return layout("AI Behavior Release Manager", body, message)


def render_pipeline(pipeline):
    stage_counts = {column["stage"]: len(column["cards"]) for column in pipeline}
    pipeline_by_stage = {column["stage"]: column for column in pipeline}
    primary_columns = render_pipeline_columns(
        [pipeline_by_stage[stage] for stage in PRIMARY_PIPELINE_STAGES]
    )
    decision_columns = render_pipeline_columns(
        [pipeline_by_stage[stage] for stage in DECISION_STAGE_NAMES]
    )
    return """
    <section id="pipeline" class="panel">
      <div class="panel-title">Behavior Release Pipeline</div>
      <p class="section-copy">CI/CD-style view of prompt and model behavior from candidate pool to local A/B, QA / A-B, canary, ramp, and production. A/B and rollout signals are seeded metadata for this MVP.</p>
      <p class="section-copy">Rollback appears on a card after that candidate enters an active rollout stage. Draft, blocked, and needs-tuning candidates have nothing live to roll back.</p>
      {}
      <div class="pipeline-row-label">Release path</div>
      <div class="pipeline-board pipeline-board-primary">{}</div>
      <div class="pipeline-row-label">Decision and follow-up lanes</div>
      <div class="pipeline-board pipeline-board-decisions">{}</div>
    </section>
    """.format(
        render_release_map(stage_counts),
        primary_columns,
        decision_columns,
    )


def render_pipeline_columns(columns):
    rendered = []
    for column in columns:
        cards = "".join(render_pipeline_card(card) for card in column["cards"])
        if not cards:
            cards = '<div class="pipeline-empty">No versions</div>'
        stage = column["stage"]
        stage_label = PIPELINE_STAGE_LABELS.get(stage, stage)
        stage_kind = PIPELINE_STAGE_KINDS.get(stage, "default")
        stage_note = ""
        if stage == "Blocked":
            stage_note = '<p class="pipeline-stage-note">Auto-rejects after 1 day without reviewer action.</p>'
        rendered.append(
            """
            <div class="pipeline-column pipeline-column-{}">
              <div class="pipeline-stage">{}</div>
              {}
              {}
            </div>
            """.format(
                h(stage_kind),
                h(stage_label),
                stage_note,
                cards,
            )
        )
    return "".join(rendered)


def render_release_map(stage_counts):
    steps = []
    for number, label, copy, active_keys in RELEASE_MAP_STAGES:
        count = sum(stage_counts.get(key, 0) for key in active_keys)
        active = " is-active" if count else ""
        activity = "Active" if count else "Step"
        steps.append(
            """
            <div class="release-step{}">
              <div class="release-step-number">{}</div>
              <div>
                <div class="release-step-title">{}</div>
                <p>{}</p>
                <span>{}</span>
              </div>
            </div>
            """.format(
                active,
                h(number),
                h(label),
                h(copy),
                h(activity),
            )
        )

    lanes = []
    for stage, label, kind, copy in DECISION_LANES:
        count = stage_counts.get(stage, 0)
        lanes.append(
            """
            <div class="decision-lane decision-lane-{}">
              <strong>{}</strong>
              <span>{} item{}</span>
              <p>{}</p>
            </div>
            """.format(
                h(kind),
                h(label),
                h(count),
                "" if count == 1 else "s",
                h(copy),
            )
        )

    return """
    <div class="release-map">
      <div class="release-map-track">{}</div>
      <div class="decision-lanes">{}</div>
    </div>
    """.format(
        "".join(steps),
        "".join(lanes),
    )


def render_pipeline_card(card):
    version = card["version"]
    version_name = display_version_name(version)
    use_case_disclosure = render_use_case_disclosure(card["use_cases"])
    red_flags = "".join("<li>{}</li>".format(h(item)) for item in card["red_flags"])
    advice = "".join("<li>{}</li>".format(h(item)) for item in card["path_to_green"])
    score = "n/a" if card["score"] is None else card["score"]
    rollout = card.get("rollout")
    feedback_health = card.get("feedback_health", {})
    feedback_health_line = """
    <div class="pipeline-feedback pipeline-feedback-{}">
      <strong>{}</strong>
      <span>{}</span>
    </div>
    """.format(
        h(feedback_health.get("state", "neutral")),
        h(feedback_health.get("label", "No feedback")),
        h(feedback_health.get("detail", "")),
    )
    prompt_details = render_pipeline_prompt_details(card)
    rollout_line = ""
    if rollout:
        rollout_line = """
        <dl class="rollout-facts">
          <div><dt>Env</dt><dd>{}</dd></div>
          <div><dt>Traffic</dt><dd>{}%</dd></div>
          <div><dt>Guardrail</dt><dd>{}</dd></div>
          <div><dt>Holdout</dt><dd>{}</dd></div>
          <div><dt>Window</dt><dd>{}m</dd></div>
          <div><dt>Status</dt><dd>{}</dd></div>
        </dl>
        """.format(
            h(rollout["environment"]),
            h(rollout["rollout_percent"]),
            h(rollout["guardrail_state"]),
            h(rollout["holdout_version_id"]),
            h(rollout["observation_window_minutes"]),
            h(rollout["status"]),
        )
    actions = ""
    if card.get("action"):
        actions += """
        <form method="post" action="/pipeline/advance">
          <input type="hidden" name="candidate_id" value="{}">
          <button class="small-action primary" type="submit">{}</button>
        </form>
        """.format(
            h(version["id"]), h(card["action"])
        )
    if card.get("rollback_action"):
        actions += """
        <form method="post" action="/pipeline/rollback">
          <input type="hidden" name="candidate_id" value="{}">
          <input type="hidden" name="reason" value="Reviewer requested rollback or hold.">
          <button class="small-action" type="submit">{}</button>
        </form>
        """.format(
            h(version["id"]), h(card["rollback_action"])
        )
    if actions:
        actions = '<div class="pipeline-actions">{}</div>'.format(actions)
    return """
    <article class="pipeline-card pipeline-{}">
      <div class="pipeline-card-header">
        <div class="pipeline-card-title" title="{}">{}</div>
        <div class="pipeline-status-row">{}</div>
      </div>
      <div class="pipeline-card-meta">Release score {}</div>
      {}
      {}
      {}
      {}
      <div class="mini-label">Red flags</div>
      <ul>{}</ul>
      <div class="mini-label">Path to green</div>
      <ul>{}</ul>
      {}
    </article>
    """.format(
        h(card["kind"]),
        h(version_name),
        h(version_name),
        badge(card["gate_state"], gate_kind(card["gate_state"])),
        h(score),
        feedback_health_line,
        rollout_line,
        prompt_details,
        use_case_disclosure,
        red_flags or "<li>None</li>",
        advice or "<li>No action required</li>",
        actions,
    )


def render_use_case_disclosure(use_cases):
    items = use_cases[:4]
    if not items:
        items = ["Not mapped yet"]
    rows = "".join("<li>{}</li>".format(h(item)) for item in items)
    count = len(use_cases)
    label = "{} use case{}".format(count or 0, "" if count == 1 else "s")
    return """
    <div class="usecase-hover" tabindex="0">
      <span>{}</span>
      <div class="usecase-popover" role="tooltip">
        <div class="mini-label">Use cases</div>
        <ul>{}</ul>
      </div>
    </div>
    """.format(
        h(label),
        rows,
    )


def render_pipeline_prompt_details(card):
    version = card["version"]
    snapshots = card.get("prompt_snapshots", [])
    snapshot_items = []
    for item in snapshots[:4]:
        snapshot_items.append(
            """
            <li>
              <strong>v{}</strong>
              <span>{} | temp {} | {}</span>
              <pre>{}</pre>
            </li>
            """.format(
                h(item["version_number"]),
                h(item["model"]),
                h(item["temperature"]),
                h(item["config_hash"]),
                h(item["prompt"]),
            )
        )
    return """
    <details class="pipeline-details">
      <summary>Prompt details</summary>
      <dl class="prompt-config-facts">
        <div><dt>Version ID</dt><dd>{}</dd></div>
        <div><dt>Model</dt><dd>{}</dd></div>
        <div><dt>Temperature</dt><dd>{}</dd></div>
        <div><dt>Config hash</dt><dd>{}</dd></div>
      </dl>
      <div class="mini-label">Current prompt</div>
      <pre>{}</pre>
      <div class="mini-label">Prompt version history</div>
      <ul class="prompt-snapshot-list">{}</ul>
    </details>
    """.format(
        h(version["id"]),
        h(version["model"]),
        h(version["temperature"]),
        h(version["config_hash"]),
        h(version["prompt"]),
        "".join(snapshot_items) or "<li>No snapshots yet.</li>",
    )


def render_versions(production, candidate, preview):
    return """
    <section class="comparison-help">
      <div>
        <strong>Production baseline</strong>
        <span>Read-only behavior currently serving users. Eval runs compare against this snapshot.</span>
      </div>
      <div class="comparison-arrow">vs</div>
      <div>
        <strong>Candidate draft</strong>
        <span>Edit prompt/model/temperature here. Changes do not affect production until the pipeline reaches Production.</span>
      </div>
    </section>
    <section id="behavior-config" class="two-column">
      <article class="panel">
        <div class="panel-title">Production Baseline - Read Only</div>
        <h3>{}</h3>
        <div class="identity-strip">
          <span>Version ID</span><strong>{}</strong>
          <span>Status</span><strong>production</strong>
        </div>
        <div class="meta-row">{} model {} | temp {} | rollout 100%</div>
        <pre>{}</pre>
      </article>
      <article class="panel">
        <div class="panel-title">Candidate Draft - Editable</div>
        <p class="section-copy">Saving candidate changes makes prior eval evidence stale. Rollout percentage is controlled by pipeline stages, not this form.</p>
        <div class="identity-strip">
          <span>Version ID</span><strong>{}</strong>
          <span>Status</span><strong>{}</strong>
        </div>
        <form method="post" action="/candidate/update">
          <input type="hidden" name="candidate_id" value="{}">
          <label>Name</label>
          <input value="{}" disabled>
          <label>Model</label>
          <input name="model" value="{}">
          <label>Temperature</label>
          <input name="temperature" type="number" step="0.1" min="0" max="2" value="{}">
          <p class="field-hint">The mock provider derives a deterministic behavior profile from prompt, model, and temperature. Temperature 1.0+ also produces a more creative, less policy-safe reply.</p>
          <div class="readonly-field">
            <span>Pipeline rollout</span>
            <strong>{}%</strong>
          </div>
          <label>Prompt</label>
          <textarea name="prompt" rows="8">{}</textarea>
          <label>Release notes</label>
          <textarea name="release_notes" rows="3">{}</textarea>
          <button type="submit">Save Candidate Draft</button>
        </form>
      </article>
    </section>
    <section id="prompt-reply" class="panel">
      <div class="panel-title">Prompt Reply Comparison</div>
      <p class="section-copy">This deterministic preview shows one representative high-risk user request before you run the full eval suite. Saving the candidate updates the right-side reply only; production changes only after the release pipeline reaches Production.</p>
      <div class="preview-question">
        <span>User asks</span>
        <strong>{}</strong>
      </div>
      <div class="two-column compact preview-grid">
        <div>
          <h4>Production baseline reply</h4>
          <pre>{}</pre>
        </div>
        <div>
          <h4>Candidate draft reply</h4>
          <pre>{}</pre>
        </div>
      </div>
    </section>
    """.format(
        h(production["name"]),
        h(production["id"]),
        badge(production["status"], "pass"),
        h(production["model"]),
        h(production["temperature"]),
        h(production["prompt"]),
        h(candidate["id"]),
        h(candidate["status"]),
        h(candidate["id"]),
        h(candidate["name"]),
        h(candidate["model"]),
        h(candidate["temperature"]),
        h(candidate["rollout_percent"]),
        h(candidate["prompt"]),
        h(candidate["release_notes"]),
        h(preview["input"]),
        h(preview["production_output"]),
        h(preview["candidate_output"]),
    )


def render_gate(eval_run, state):
    candidate = state["candidate"]
    if not eval_run:
        return """
        <section class="panel">
          <div class="panel-title">Release Gate</div>
          <h3>Not evaluated</h3>
          <p>Run an eval comparison before this candidate can be promoted.</p>
        </section>
        """
    gate = eval_run["gate"]
    blockers = "".join("<li>{}</li>".format(h(item)) for item in gate.get("blockers", []))
    warnings = "".join("<li>{}</li>".format(h(item)) for item in gate.get("warnings", []))
    stale = '<p class="warning">This eval is stale. Rerun before promotion.</p>' if state["stale"] else ""
    return """
    <section class="panel gate gate-{}">
      <div class="panel-title">Release Gate</div>
      <div class="gate-header">
        <h3>{}</h3>
        {}
      </div>
      {}
      <div class="two-column compact">
        <div>
          <h4>Blockers</h4>
          <ul>{}</ul>
        </div>
        <div>
          <h4>Warnings</h4>
          <ul>{}</ul>
        </div>
      </div>
      <div class="actions">
        <p class="section-copy">Use the pipeline actions above to advance this candidate through local A/B, QA / A-B, canary, ramp, and production.</p>
        <form method="post" action="/reject">
          <input type="hidden" name="candidate_id" value="{}">
          <button type="submit">Reject</button>
        </form>
      </div>
    </section>
    """.format(
        h(gate["state"]),
        h(gate["state"].upper()),
        gate_badge(gate["state"]),
        stale,
        blockers or "<li>None</li>",
        warnings or "<li>None</li>",
        h(candidate["id"]),
    )


def render_eval_results(eval_run):
    if not eval_run:
        return ""
    rows = []
    for item in eval_run["results"]:
        kind = "bad" if item["classification"] == "regressed" else "good" if item["classification"] == "improved" else "neutral"
        rows.append(
            """
            <details class="result-row">
              <summary>
                <span>{}</span>
                {}
                {}
                <span>candidate {} / production {}</span>
              </summary>
              <div class="result-detail">
                <div><h4>Production output</h4><pre>{}</pre></div>
                <div><h4>Candidate output</h4><pre>{}</pre></div>
                <p>{}</p>
              </div>
            </details>
            """.format(
                h(item["case_id"]),
                badge(item["severity"], item["severity"]),
                badge(item["classification"], kind),
                h(item["candidate_score"]),
                h(item["production_score"]),
                h(item["production_output"]),
                h(item["candidate_output"]),
                h(item["reason"]),
            )
        )
    return """
    <section id="eval-comparison" class="panel">
      <div class="panel-title">Eval Comparison</div>
      <h3>Apples-to-apples run {}</h3>
      <div class="meta-row">provider {} | evaluator {} | suite {}</div>
      {}
    </section>
    """.format(
        h(eval_run["id"]),
        h(eval_run["provider_snapshot"]),
        h(eval_run["evaluator_version"]),
        h(eval_run["eval_suite_hash"]),
        "".join(rows),
    )


def render_evidence(evidence):
    if not evidence:
        return ""
    rows = []
    for item in evidence:
        rows.append(
            """
            <tr>
              <td>{}</td>
              <td>{}</td>
              <td>{}</td>
              <td>{}</td>
              <td>{}</td>
              <td>{}</td>
            </tr>
            """.format(
                h(item["feature"]),
                h(item["use_case"]),
                fmt_pct(item["accuracy"]),
                h(item["regression_count"]),
                h(item["critical_regression_count"]),
                badge(item["status"], "pass" if item["status"] == "fresh" else "warn"),
            )
        )
    return """
    <section id="release-evidence" class="panel">
      <div class="panel-title">Release Evidence Index</div>
      <p>Local derived cache. Generic per workspace, rebuilt from raw eval results and feedback inputs.</p>
      <table>
        <thead><tr><th>Feature</th><th>Use case</th><th>Accuracy</th><th>Regressions</th><th>Critical</th><th>Cache</th></tr></thead>
        <tbody>{}</tbody>
      </table>
    </section>
    """.format(
        "".join(rows)
    )


def render_feedback(state):
    weights = state["weights"]
    score = state["feedback_score"]
    prod = state["production_feedback"]
    cand = state["candidate_feedback"]
    worsened = "".join("<li>{}</li>".format(h(item)) for item in score["high_risk_worsened"]) if score else ""
    prompt_events = render_prompt_feedback_events(
        state["candidate_prompt_feedback"],
        state["production_prompt_feedback"],
        state["candidate_prompt_feedback_summary"],
    )
    return """
    <section id="prompt-feedback" class="panel">
      <div class="panel-title">Prompt Feedback Store</div>
      <p class="section-copy">Concrete feedback events captured from QA, canary, ramp, or production. In the MVP these are seeded telemetry-like records; in production they would be ingested from real rollout exposure and used to tune the next prompt candidate.</p>
      {}
    </section>
    <section id="feedback" class="two-column">
      <article class="panel">
        <div class="panel-title">Feedback Signals</div>
        <table>
          <thead><tr><th>Metric</th><th>Production</th><th>Candidate</th></tr></thead>
          <tbody>
            <tr><td>Thumbs up</td><td>{}</td><td>{}</td></tr>
            <tr><td>Thumbs down</td><td>{}</td><td>{}</td></tr>
            <tr><td>Escalation</td><td>{}</td><td>{}</td></tr>
            <tr><td>Retry</td><td>{}</td><td>{}</td></tr>
          </tbody>
        </table>
        <h4>Mixed-signal warnings</h4>
        <ul>{}</ul>
      </article>
      <article class="panel">
        <div class="panel-title">Feedback Weighting</div>
        <form method="post" action="/weights/update">
          {}
          <button type="submit">Update Weights</button>
        </form>
      </article>
    </section>
    """.format(
        prompt_events,
        fmt_pct(prod["thumbs_up_rate"]),
        fmt_pct(cand["thumbs_up_rate"]),
        fmt_pct(prod["thumbs_down_rate"]),
        fmt_pct(cand["thumbs_down_rate"]),
        fmt_pct(prod["escalation_rate"]),
        fmt_pct(cand["escalation_rate"]),
        fmt_pct(prod["retry_rate"]),
        fmt_pct(cand["retry_rate"]),
        worsened or "<li>None</li>",
        "".join(weight_input(key, value) for key, value in weights.items()),
    )


def render_prompt_feedback_events(candidate_events, production_events, summary):
    def event_card(event):
        return """
        <article class="feedback-event feedback-{}">
          <div class="feedback-event-top">
            {}
            <span>{} | {} | {}%</span>
          </div>
          <div class="source-trust">
            <span>{}</span>
            <strong>weight {}</strong>
          </div>
          <strong>{}</strong>
          <p>{}</p>
          <p>{}</p>
          <div class="mini-label">Suggested prompt change</div>
          <p>{}</p>
        </article>
        """.format(
            h(event["sentiment"]),
            badge(event["sentiment"], "pass" if event["sentiment"] == "positive" else "warn" if event["sentiment"] == "neutral" else "block"),
            h(event["source"]),
            h(event["rollout_stage"]),
            h(event["traffic_percent"]),
            h(event["source_type"]),
            h(event["source_weight"]),
            h(event["use_case"]),
            h(event["feedback_text"]),
            h(event["weight_reason"]),
            h(event["suggested_prompt_change"]),
        )

    candidate_cards = "".join(event_card(event) for event in candidate_events)
    production_cards = "".join(event_card(event) for event in production_events)
    return """
    <div class="feedback-source-policy">
      <div><span>Production rollout events</span><strong>{}</strong><p>Canary, ramp, and production signals use full weight because they reflect real exposure.</p></div>
      <div><span>Local proxy events</span><strong>{}</strong><p>Historical/ML/reviewer feedback uses lower weight unless it flags refund, safety, or escalation risk.</p></div>
      <div><span>High-risk red flags</span><strong>{}</strong><p>Negative local signals on high-risk features are promoted above normal local feedback.</p></div>
      <div><span>Weighted negative signal</span><strong>{}</strong><p>Directional triage score for prompt follow-up, not a statistical production decision.</p></div>
    </div>
    <div class="two-column compact feedback-event-grid">
      <div>
        <h4>Candidate feedback</h4>
        {}
      </div>
      <div>
        <h4>Production baseline feedback</h4>
        {}
      </div>
    </div>
    """.format(
        h(summary["production_rollout_count"]),
        h(summary["local_proxy_count"]),
        h(summary["red_flag_count"]),
        h(summary["weighted_negative_signal"]),
        candidate_cards or '<p class="section-copy">No candidate feedback captured yet.</p>',
        production_cards or '<p class="section-copy">No production feedback captured yet.</p>',
    )


def weight_input(key, value):
    return """
    <label>{}</label>
    <input name="{}" type="number" min="0" max="3" step="0.05" value="{}">
    """.format(
        h(key.replace("_", " ")), h(key), h(value)
    )


def render_audit(events):
    rows = []
    for event in events:
        details = render_audit_details(event.get("details", {}))
        rows.append(
            """
            <li>
              <strong>{}</strong>
              <span>{}</span>
              <em>{}</em>
              {}
            </li>
            """.format(
                h(event["event_type"]),
                h(event["message"]),
                h(event["created_at"]),
                details,
            )
        )
    return """
    <section id="audit" class="panel">
      <div class="panel-title">Audit Timeline</div>
      <ol class="timeline">{}</ol>
    </section>
    """.format(
        "".join(rows)
    )


def render_audit_details(details):
    candidate = details.get("candidate")
    snapshot = details.get("prompt_snapshot")
    facts = []

    if candidate:
        facts.extend(
            [
                ("Candidate", candidate["name"]),
                ("Status", details.get("candidate_status")),
                ("Rollout", "{}%".format(details.get("rollout_percent", 0))),
            ]
        )
    if details.get("eval_run_id"):
        facts.extend(
            [
                ("Eval run", details.get("eval_run_id")),
                ("Eval status", details.get("eval_status")),
                ("Gate", details.get("gate_state")),
            ]
        )
    if details.get("release_score") is not None:
        facts.append(("Release score", details.get("release_score")))
    if details.get("candidate_pass_rate") is not None:
        facts.append(("Pass rate", fmt_pct(details.get("candidate_pass_rate"))))
    if details.get("regressions") is not None:
        facts.append(("Regressions", details.get("regressions")))
    if details.get("critical_regressions") is not None:
        facts.append(("Critical", details.get("critical_regressions")))
    if details.get("rollout_stage"):
        facts.append(("Stage", details.get("rollout_stage")))
    if details.get("guardrail_state"):
        facts.append(("Guardrail", details.get("guardrail_state")))
    if details.get("holdout_version_id"):
        facts.append(("Holdout", details.get("holdout_version_id")))
    if details.get("rollback_target_version_id"):
        facts.append(("Rollback target", details.get("rollback_target_version_id")))
    if details.get("reason"):
        facts.append(("Reason", details.get("reason")))

    blockers = details.get("blockers") or []
    warnings = details.get("warnings") or []
    if blockers:
        facts.append(("Blockers", "; ".join(blockers)))
    if warnings:
        facts.append(("Warnings", "; ".join(warnings)))

    if not facts and not snapshot:
        return ""

    fact_html = "".join(
        "<div><dt>{}</dt><dd>{}</dd></div>".format(h(label), h(value))
        for label, value in facts
        if value not in (None, "")
    )

    prompt_html = ""
    if snapshot:
        prompt_html = """
        <details class="audit-prompt-details">
          <summary>Prompt snapshot v{} · {} · temp {}</summary>
          <dl class="audit-facts">
            <div><dt>Config hash</dt><dd>{}</dd></div>
            <div><dt>Captured</dt><dd>{}</dd></div>
            <div><dt>Change note</dt><dd>{}</dd></div>
          </dl>
          <pre>{}</pre>
        </details>
        """.format(
            h(snapshot["version_number"]),
            h(snapshot["model"]),
            h(snapshot["temperature"]),
            h(details.get("config_hash") or snapshot["config_hash"]),
            h(snapshot["created_at"]),
            h(snapshot["change_note"]),
            h(snapshot["prompt"]),
        )

    return """
    <details class="audit-context">
      <summary>Details</summary>
      <dl class="audit-facts">{}</dl>
      {}
    </details>
    """.format(
        fact_html,
        prompt_html,
    )


def render_cases(cases):
    rows = []
    for case in cases:
        rows.append(
            """
            <tr>
              <td>{}</td>
              <td>{}</td>
              <td>{}</td>
              <td>{}</td>
              <td>{}</td>
            </tr>
            """.format(
                h(case["name"]),
                badge(case["severity"], case["severity"]),
                h(case["feature"]),
                h(case["use_case"]),
                h(", ".join(case["tags"])),
            )
        )
    return layout(
        "Eval Suite",
        """
        <section class="panel">
          <div class="panel-title">Seeded Eval Suite</div>
          <table>
            <thead><tr><th>Case</th><th>Severity</th><th>Feature</th><th>Use case</th><th>Tags</th></tr></thead>
            <tbody>{}</tbody>
          </table>
        </section>
        """.format(
            "".join(rows)
        ),
    )
