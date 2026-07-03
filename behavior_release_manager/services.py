from datetime import datetime, timedelta, timezone

from . import db, evaluator, mock_model, release_gate
from .seed import DEFAULT_WEIGHTS_ID, behavior_hash
from .utils import new_id, now_iso, stable_hash


PIPELINE_STAGES = [
    "Candidate Pool",
    "Local A/B Simulation",
    "QA / A-B Test",
    "Prod Canary 10%",
    "Prod Ramp 50%",
    "Production",
    "Needs Tuning",
    "Blocked",
    "Rejected",
]


ROLLOUT_SEQUENCE = [
    ("local_ab", "Local A/B Simulation", "local", 0, "Run Local A/B", 10),
    ("qa_ab", "QA / A-B Test", "qa", 0, "Submit to QA / A-B", 60),
    ("production_10", "Prod Canary 10%", "production", 10, "Start 10% Canary", 120),
    ("production_50", "Prod Ramp 50%", "production", 50, "Ramp to 50%", 240),
    ("production_100", "Production", "production", 100, "Ramp to 100%", 1440),
]


def rollout_def_tuple_to_dict(item):
    stage, label, environment, rollout_percent, action, window = item
    return {
        "stage": stage,
        "label": label,
        "environment": environment,
        "rollout_percent": rollout_percent,
        "action": action,
        "window": window,
    }


ROLLOUT_DEFS = [rollout_def_tuple_to_dict(item) for item in ROLLOUT_SEQUENCE]
ROLLOUT_BY_STAGE = {item["stage"]: item for item in ROLLOUT_DEFS}
HIGH_RISK_FEEDBACK_FEATURES = {"Refund policy", "Safety boundaries", "Escalation"}


def hydrate_version(row):
    item = db.row_to_dict(row)
    return item


def hydrate_prompt_snapshot(row):
    return db.row_to_dict(row)


def hydrate_case(row):
    item = db.row_to_dict(row)
    item["tags"] = db.loads(item.pop("tags_json"), [])
    item["must_include"] = db.loads(item.pop("must_include_json"), [])
    item["must_not_include"] = db.loads(item.pop("must_not_include_json"), [])
    return item


def hydrate_feedback(row):
    item = db.row_to_dict(row)
    item["complaint_tags"] = db.loads(item.pop("complaint_tags_json"), {})
    return item


def hydrate_prompt_feedback(row):
    item = db.row_to_dict(row)
    source_type, weight, reason = classify_feedback_event(item)
    item["source_type"] = source_type
    item["source_weight"] = weight
    item["weight_reason"] = reason
    return item


def classify_feedback_event(event):
    production_like = (
        event["environment"] == "production"
        or "canary" in event["source"]
        or "ramp" in event["source"]
        or "production" in event["source"]
    )
    if production_like:
        return (
            "production rollout",
            1.0,
            "Real or production-like rollout signal; treat as high trust.",
        )
    if (
        event["sentiment"] == "negative"
        and event["feature"] in HIGH_RISK_FEEDBACK_FEATURES
    ):
        return (
            "local proxy with red flag",
            0.75,
            "Local/ML/reviewer signal, but high-risk feature red flag raises priority.",
        )
    return (
        "local proxy",
        0.35,
        "Offline, ML-derived, or reviewer signal; useful early but lower confidence.",
    )


def hydrate_weights(row):
    return {
        "thumbs_up_rate": row["thumbs_up_rate"],
        "thumbs_down_rate": row["thumbs_down_rate"],
        "escalation_rate": row["escalation_rate"],
        "retry_rate": row["retry_rate"],
        "complaint_tags": row["complaint_tags"],
        "conflict_penalty": row["conflict_penalty"],
    }


def audit(conn, event_type, message, metadata=None):
    conn.execute(
        """
        INSERT INTO audit_events (id, event_type, message, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            new_id("audit"),
            event_type,
            message,
            db.dumps(metadata or {}),
            now_iso(),
        ),
    )


def reset_demo_data(conn):
    with db.transaction(conn):
        for table in [
            "approved_prompt_registry",
            "rollout_stages",
            "prompt_feedback_events",
            "release_evidence_index",
            "eval_results",
            "eval_runs",
            "audit_events",
            "feedback_signals",
            "feedback_weight_configs",
            "feature_use_case_map",
            "eval_cases",
            "prompt_config_snapshots",
            "behavior_versions",
        ]:
            conn.execute("DELETE FROM {}".format(table))


def list_versions(conn):
    return [
        hydrate_version(row)
        for row in conn.execute(
            "SELECT * FROM behavior_versions ORDER BY status = 'production' DESC, updated_at DESC"
        )
    ]


def get_version(conn, version_id):
    return hydrate_version(
        conn.execute("SELECT * FROM behavior_versions WHERE id = ?", (version_id,)).fetchone()
    )


def base_name_without_version(name):
    head, sep, tail = name.rpartition(" v")
    if sep and tail.isdigit():
        return head
    return name


def list_prompt_snapshots(conn, version_id):
    return [
        hydrate_prompt_snapshot(row)
        for row in conn.execute(
            """
            SELECT * FROM prompt_config_snapshots
            WHERE behavior_version_id = ?
            ORDER BY version_number DESC
            """,
            (version_id,),
        )
    ]


def latest_prompt_version_number(conn, version_id):
    row = conn.execute(
        """
        SELECT COALESCE(MAX(version_number), 0) AS version_number
        FROM prompt_config_snapshots
        WHERE behavior_version_id = ?
        """,
        (version_id,),
    ).fetchone()
    return int(row["version_number"])


def insert_prompt_snapshot(conn, version_id, version_number, prompt, model, temperature, config_hash, change_note):
    conn.execute(
        """
        INSERT INTO prompt_config_snapshots (
            id, behavior_version_id, version_number, prompt, model,
            temperature, config_hash, change_note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("snap"),
            version_id,
            version_number,
            prompt,
            model,
            temperature,
            config_hash,
            change_note,
            now_iso(),
        ),
    )


def get_production(conn):
    return hydrate_version(
        conn.execute(
            "SELECT * FROM behavior_versions WHERE status = 'production' LIMIT 1"
        ).fetchone()
    )


def list_eval_cases(conn):
    return [
        hydrate_case(row)
        for row in conn.execute(
            "SELECT * FROM eval_cases ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, feature"
        )
    ]


def latest_rollout_stage(conn, version_id):
    row = conn.execute(
        """
        SELECT * FROM rollout_stages
        WHERE behavior_version_id = ?
        ORDER BY started_at DESC, rowid DESC
        LIMIT 1
        """,
        (version_id,),
    ).fetchone()
    item = db.row_to_dict(row)
    if item:
        item["metrics"] = db.loads(item.pop("metrics_json"), {})
    return item


def rollout_definition(stage):
    return ROLLOUT_BY_STAGE.get(stage)


def next_rollout_definition(current_stage=None):
    if current_stage is None:
        return ROLLOUT_DEFS[0]
    for index, item in enumerate(ROLLOUT_DEFS):
        if item["stage"] == current_stage and index + 1 < len(ROLLOUT_DEFS):
            return ROLLOUT_DEFS[index + 1]
    return None


def get_feedback(conn, version_id):
    return hydrate_feedback(
        conn.execute(
            "SELECT * FROM feedback_signals WHERE behavior_version_id = ?",
            (version_id,),
        ).fetchone()
    )


def list_prompt_feedback(conn, version_id, limit=5):
    return [
        hydrate_prompt_feedback(row)
        for row in conn.execute(
            """
            SELECT * FROM prompt_feedback_events
            WHERE behavior_version_id = ?
            ORDER BY
                CASE sentiment WHEN 'negative' THEN 0 WHEN 'neutral' THEN 1 ELSE 2 END,
                traffic_percent DESC,
                created_at DESC
            LIMIT ?
            """,
            (version_id, limit),
        )
    ]


def summarize_prompt_feedback(events):
    summary = {
        "production_rollout_count": 0,
        "local_proxy_count": 0,
        "red_flag_count": 0,
        "weighted_negative_signal": 0.0,
    }
    for event in events:
        if event["source_type"] == "production rollout":
            summary["production_rollout_count"] += 1
        else:
            summary["local_proxy_count"] += 1
        if event["sentiment"] == "negative":
            summary["weighted_negative_signal"] += event["source_weight"]
            if event["feature"] in HIGH_RISK_FEEDBACK_FEATURES:
                summary["red_flag_count"] += 1
    summary["weighted_negative_signal"] = round(
        summary["weighted_negative_signal"], 2
    )
    return summary


def feedback_health_for_version(conn, version_id):
    events = list_prompt_feedback(conn, version_id, limit=8)
    if not events:
        return {
            "state": "neutral",
            "label": "No rollout feedback",
            "detail": "No feedback events captured yet.",
            "negative_count": 0,
            "positive_count": 0,
        }

    production_events = [
        event for event in events if event["source_type"] == "production rollout"
    ]
    local_red_flags = [
        event
        for event in events
        if event["source_type"] == "local proxy with red flag"
        and event["sentiment"] == "negative"
    ]
    signal_events = production_events or events
    negative_count = sum(1 for event in signal_events if event["sentiment"] == "negative")
    positive_count = sum(1 for event in signal_events if event["sentiment"] == "positive")

    if production_events and negative_count > 0:
        return {
            "state": "block",
            "label": "Production feedback risk",
            "detail": "{} negative rollout signal(s). Hold or rollback before ramping.".format(
                negative_count
            ),
            "negative_count": negative_count,
            "positive_count": positive_count,
        }
    if production_events and positive_count > 0 and negative_count == 0:
        return {
            "state": "pass",
            "label": "Production feedback healthy",
            "detail": "{} positive rollout signal(s). Continue if guardrails remain healthy.".format(
                positive_count
            ),
            "negative_count": negative_count,
            "positive_count": positive_count,
        }
    if production_events:
        return {
            "state": "neutral",
            "label": "Production feedback observed",
            "detail": "{} neutral rollout signal(s). Monitor trend before changing baseline.".format(
                len(production_events)
            ),
            "negative_count": negative_count,
            "positive_count": positive_count,
        }
    if local_red_flags:
        return {
            "state": "warn",
            "label": "Local feedback red flag",
            "detail": "{} high-risk local signal(s). Review before rollout.".format(
                len(local_red_flags)
            ),
            "negative_count": len(local_red_flags),
            "positive_count": positive_count,
        }
    return {
        "state": "neutral",
        "label": "Local feedback only",
        "detail": "Directional local feedback only; wait for rollout telemetry.",
        "negative_count": negative_count,
        "positive_count": positive_count,
    }


def simulate_rollout_metrics(conn, candidate_id, stage):
    production = get_production(conn)
    prod_feedback = get_feedback(conn, production["id"])
    cand_feedback = get_feedback(conn, candidate_id)
    weights = get_weights(conn)
    feedback = release_gate.score_feedback(prod_feedback, cand_feedback, weights)
    guardrail_state = "healthy"
    reasons = []
    deltas = feedback["deltas"]
    if deltas["escalation_rate"] < -0.05:
        guardrail_state = "rollback_required"
        reasons.append("Escalation rate worsened by more than 5 percentage points.")
    elif deltas["thumbs_down_rate"] < -0.03 or feedback["mixed_signal_penalty"] > 0:
        guardrail_state = "warning"
        reasons.append("Feedback guardrail warning against holdout.")
    if stage in ("production_10", "production_50") and guardrail_state != "healthy":
        reasons.append("Production ramp cannot advance while guardrails are unhealthy.")
    return {
        "guardrail_state": guardrail_state,
        "reasons": reasons,
        "feedback": feedback,
        "holdout_version_id": production["id"],
    }


def get_weights(conn):
    row = conn.execute(
        "SELECT * FROM feedback_weight_configs WHERE id = ?", (DEFAULT_WEIGHTS_ID,)
    ).fetchone()
    return hydrate_weights(row)


def update_weights(conn, form):
    now = now_iso()
    values = {}
    for key in [
        "thumbs_up_rate",
        "thumbs_down_rate",
        "escalation_rate",
        "retry_rate",
        "complaint_tags",
        "conflict_penalty",
    ]:
        values[key] = float(form.get(key, "1") or "1")
    conn.execute(
        """
        UPDATE feedback_weight_configs
        SET thumbs_up_rate = ?, thumbs_down_rate = ?, escalation_rate = ?,
            retry_rate = ?, complaint_tags = ?, conflict_penalty = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            values["thumbs_up_rate"],
            values["thumbs_down_rate"],
            values["escalation_rate"],
            values["retry_rate"],
            values["complaint_tags"],
            values["conflict_penalty"],
            now,
            DEFAULT_WEIGHTS_ID,
        ),
    )
    audit(conn, "feedback_weights_updated", "Updated feedback release-gate weights.", values)
    conn.commit()


def update_candidate(conn, version_id, form):
    version = get_version(conn, version_id)
    if version["status"] == "production":
        raise ValueError("Production versions cannot be edited in this MVP.")

    prompt = form.get("prompt", version["prompt"])
    model = form.get("model", version["model"])
    temperature = float(form.get("temperature", version["temperature"]))
    rollout_percent = int(form.get("rollout_percent", version["rollout_percent"]))
    release_notes = form.get("release_notes", version["release_notes"])
    new_hash = behavior_hash(prompt, model, temperature)
    now = now_iso()
    config_changed = new_hash != version["config_hash"]
    status = "draft" if config_changed else version["status"]
    next_version_number = latest_prompt_version_number(conn, version_id)
    name = version["name"]
    if config_changed:
        next_version_number += 1
        name = "{} v{}".format(base_name_without_version(version["name"]), next_version_number)

    with db.transaction(conn):
        conn.execute(
            """
            UPDATE behavior_versions
            SET name = ?, prompt = ?, model = ?, temperature = ?, rollout_percent = ?,
                release_notes = ?, config_hash = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                prompt,
                model,
                temperature,
                rollout_percent,
                release_notes,
                new_hash,
                status,
                now,
                version_id,
            ),
        )
        if config_changed:
            insert_prompt_snapshot(
                conn,
                version_id,
                next_version_number,
                prompt,
                model,
                temperature,
                new_hash,
                "Saved candidate config from UI.",
            )
        audit(
            conn,
            "version_updated",
            "Updated {}. Prior eval evidence may be stale.".format(name),
            {
                "version_id": version_id,
                "config_hash": new_hash,
                "prompt_version": next_version_number,
                "config_changed": config_changed,
            },
        )


def eval_suite_hash(cases):
    return stable_hash(
        [
            {
                "id": case["id"],
                "must_include": case["must_include"],
                "must_not_include": case["must_not_include"],
                "severity": case["severity"],
            }
            for case in cases
        ]
    )


def run_eval(conn, candidate_id):
    production = get_production(conn)
    candidate = get_version(conn, candidate_id)
    cases = list_eval_cases(conn)
    run_id = new_id("eval")
    created_at = now_iso()
    suite_hash = eval_suite_hash(cases)
    run_config_hash = stable_hash(
        {
            "provider_snapshot": mock_model.PROVIDER_SNAPSHOT,
            "evaluator_version": evaluator.EVALUATOR_VERSION,
            "production": production["config_hash"],
            "candidate": candidate["config_hash"],
            "suite": suite_hash,
        }
    )

    result_rows = []
    for case in cases:
        production_output = mock_model.generate(production, case["input"])
        candidate_output = mock_model.generate(candidate, case["input"])
        production_eval = evaluator.evaluate_output(production_output, case)
        candidate_eval = evaluator.evaluate_output(candidate_output, case)
        classification = evaluator.classify_result(production_eval, candidate_eval)
        reason = "Production: {} Candidate: {}".format(
            production_eval["reason"], candidate_eval["reason"]
        )
        result_rows.append(
            {
                "case_id": case["id"],
                "production_output": production_output,
                "candidate_output": candidate_output,
                "production_score": production_eval["score"],
                "candidate_score": candidate_eval["score"],
                "production_passed": production_eval["passed"],
                "candidate_passed": candidate_eval["passed"],
                "classification": classification,
                "reason": reason,
                "feature": case["feature"],
                "use_case": case["use_case"],
                "severity": case["severity"],
            }
        )

    summary = evaluator.summarize_results(result_rows)
    prod_feedback = get_feedback(conn, production["id"])
    cand_feedback = get_feedback(conn, candidate["id"])
    weights = get_weights(conn)
    feedback_score = release_gate.score_feedback(prod_feedback, cand_feedback, weights)
    gate = release_gate.evaluate_gate(summary, feedback_score)
    gate["feedback"] = feedback_score

    with db.transaction(conn):
        conn.execute(
            """
            INSERT INTO eval_runs (
                id, production_version_id, candidate_version_id, status, created_at,
                completed_at, provider_mode, provider_snapshot, evaluator_version,
                eval_suite_hash, production_config_hash, candidate_config_hash,
                run_config_hash, summary_json, gate_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                production["id"],
                candidate["id"],
                "completed",
                created_at,
                now_iso(),
                mock_model.PROVIDER_MODE,
                mock_model.PROVIDER_SNAPSHOT,
                evaluator.EVALUATOR_VERSION,
                suite_hash,
                production["config_hash"],
                candidate["config_hash"],
                run_config_hash,
                db.dumps(summary),
                db.dumps(gate),
            ),
        )
        for item in result_rows:
            conn.execute(
                """
                INSERT INTO eval_results (
                    eval_run_id, case_id, production_output, candidate_output,
                    production_score, candidate_score, production_passed,
                    candidate_passed, classification, reason, feature, use_case, severity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    item["case_id"],
                    item["production_output"],
                    item["candidate_output"],
                    item["production_score"],
                    item["candidate_score"],
                    int(item["production_passed"]),
                    int(item["candidate_passed"]),
                    item["classification"],
                    item["reason"],
                    item["feature"],
                    item["use_case"],
                    item["severity"],
                ),
            )
        conn.execute(
            "UPDATE behavior_versions SET status = ?, updated_at = ? WHERE id = ? AND status != 'production'",
            ("evaluated", now_iso(), candidate["id"]),
        )
        audit(
            conn,
            "eval_run_completed",
            "Completed eval comparison for {}.".format(candidate["name"]),
            {"eval_run_id": run_id, "candidate_id": candidate["id"], "gate": gate["state"]},
        )

    rebuild_evidence_index(conn, run_id)
    return run_id


def latest_eval_for_candidate(conn, candidate_id):
    row = conn.execute(
        """
        SELECT * FROM eval_runs
        WHERE candidate_version_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    return db.row_to_dict(row)


def get_eval_run(conn, run_id):
    run = db.row_to_dict(
        conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    )
    if not run:
        return None
    run["summary"] = db.loads(run.pop("summary_json"), {})
    run["gate"] = db.loads(run.pop("gate_json"), {})
    run["results"] = [
        db.row_to_dict(row)
        for row in conn.execute(
            "SELECT * FROM eval_results WHERE eval_run_id = ? ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, case_id",
            (run_id,),
        )
    ]
    return run


def prompt_snapshot_for_hash(conn, version_id, config_hash=None):
    if config_hash:
        row = conn.execute(
            """
            SELECT * FROM prompt_config_snapshots
            WHERE behavior_version_id = ? AND config_hash = ?
            ORDER BY version_number DESC
            LIMIT 1
            """,
            (version_id, config_hash),
        ).fetchone()
        if row:
            return db.row_to_dict(row)
    row = conn.execute(
        """
        SELECT * FROM prompt_config_snapshots
        WHERE behavior_version_id = ?
        ORDER BY version_number DESC
        LIMIT 1
        """,
        (version_id,),
    ).fetchone()
    return db.row_to_dict(row)


def gate_for_candidate(conn, candidate_id):
    candidate = get_version(conn, candidate_id)
    run_row = latest_eval_for_candidate(conn, candidate_id)
    if not run_row:
        return (
            None,
            None,
            True,
            {
                "state": "block",
                "blockers": ["Run an eval comparison before promotion."],
                "warnings": [],
                "release_score": 0,
            },
        )
    run = get_eval_run(conn, run_row["id"])
    stale = is_eval_stale(conn, run_row, candidate)
    partial = run_row["status"] != "completed"
    gate = release_gate.evaluate_gate(
        run["summary"],
        run["gate"].get("feedback", {}),
        comparable=True,
        stale=stale,
        partial=partial,
    )
    return run_row, run, stale, gate


def list_audit_events(conn, limit=25):
    events = []
    for row in conn.execute(
        "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (limit,)
    ):
        event = dict(row, metadata=db.loads(row["metadata_json"], {}))
        event["details"] = audit_event_details(conn, event)
        events.append(event)
    return events


def audit_event_details(conn, event):
    metadata = event["metadata"]
    candidate_id = (
        metadata.get("candidate_id")
        or metadata.get("version_id")
        or metadata.get("approved_behavior_version_id")
    )
    eval_run_id = metadata.get("eval_run_id")
    run = get_eval_run(conn, eval_run_id) if eval_run_id else None
    if run and not candidate_id:
        candidate_id = run["candidate_version_id"]

    version = get_version(conn, candidate_id) if candidate_id else None
    if version and not run:
        latest_run = latest_eval_for_candidate(conn, version["id"])
        run = get_eval_run(conn, latest_run["id"]) if latest_run else None

    config_hash = (
        metadata.get("config_hash")
        or (run["candidate_config_hash"] if run else None)
        or (version["config_hash"] if version else None)
    )
    snapshot = (
        prompt_snapshot_for_hash(conn, version["id"], config_hash) if version else None
    )

    summary = run["summary"] if run else {}
    gate = run["gate"] if run else {}
    return {
        "candidate": version,
        "eval_run": run,
        "prompt_snapshot": snapshot,
        "candidate_status": version["status"] if version else None,
        "rollout_percent": version["rollout_percent"] if version else None,
        "eval_run_id": run["id"] if run else eval_run_id,
        "eval_status": run["status"] if run else None,
        "gate_state": metadata.get("gate")
        or metadata.get("gate_state")
        or gate.get("state"),
        "release_score": gate.get("release_score"),
        "candidate_pass_rate": summary.get("candidate_pass_rate"),
        "regressions": summary.get("regressions"),
        "critical_regressions": summary.get("critical_regressions"),
        "rollout_stage": metadata.get("stage"),
        "guardrail_state": metadata.get("guardrail_state"),
        "holdout_version_id": metadata.get("holdout_version_id"),
        "rollback_target_version_id": metadata.get("rollback_target_version_id"),
        "reason": metadata.get("reason"),
        "config_hash": config_hash,
        "prompt_version": metadata.get("prompt_version")
        or (snapshot["version_number"] if snapshot else None),
        "blockers": metadata.get("blockers", []),
        "warnings": metadata.get("warnings", []),
    }


def list_evidence(conn, eval_run_id=None):
    if eval_run_id:
        rows = conn.execute(
            "SELECT * FROM release_evidence_index WHERE eval_run_id = ? ORDER BY feature, use_case",
            (eval_run_id,),
        )
    else:
        rows = conn.execute(
            "SELECT * FROM release_evidence_index ORDER BY computed_at DESC, feature LIMIT 20"
        )
    return [db.row_to_dict(row) for row in rows]


def path_to_green(run, stale=False):
    if not run:
        return ["Run an eval comparison against current production."]
    gate = run["gate"]
    advice = []
    if stale:
        advice.append("Rerun eval because the candidate or production config changed.")
    for blocker in gate.get("blockers", []):
        if "critical regression" in blocker:
            advice.append("Inspect critical cases and add explicit policy/safety language before rerunning.")
        elif "pass rate" in blocker:
            advice.append("Fix failed eval cases until candidate pass rate matches or exceeds production.")
        elif "comparable" in blocker:
            advice.append("Rerun production and candidate under the same suite/provider snapshot.")
        else:
            advice.append(blocker)
    for warning in gate.get("warnings", []):
        if "Mixed feedback" in warning:
            advice.append("Review high-risk feedback deltas before staging.")
        elif "feedback" in warning:
            advice.append("Tune prompt to reduce negative feedback signals or lower rollout.")
        else:
            advice.append(warning)
    return advice[:3] or ["No action required."]


def rollout_path_to_green(latest_rollout):
    if not latest_rollout:
        return []
    metrics = latest_rollout.get("metrics", {})
    reasons = metrics.get("reasons", [])
    if latest_rollout["guardrail_state"] == "healthy":
        return ["Guardrails healthy. Continue to next stage when review is complete."]
    if latest_rollout["guardrail_state"] == "warning":
        return reasons or ["Hold rollout and inspect candidate-vs-holdout metrics."]
    return reasons or ["Rollback to previous production version."]


def intended_use_cases_for_version(conn, version_id):
    run_row = latest_eval_for_candidate(conn, version_id)
    if run_row:
        evidence = list_evidence(conn, run_row["id"])
        if evidence:
            return sorted({item["use_case"] for item in evidence})
    rows = conn.execute(
        "SELECT use_case FROM feature_use_case_map ORDER BY use_case LIMIT 2"
    ).fetchall()
    return [row["use_case"] for row in rows]


def pipeline_card_for_version(conn, version):
    latest_rollout = latest_rollout_stage(conn, version["id"])
    feedback_health = feedback_health_for_version(conn, version["id"])
    prompt_snapshots = list_prompt_snapshots(conn, version["id"])
    if version["status"] == "production":
        return {
            "stage": "Production",
            "kind": "production",
            "version": version,
            "gate_state": "baseline",
            "score": None,
            "red_flags": [],
            "path_to_green": ["Current production baseline. Monitor feedback and audit history."],
            "use_cases": intended_use_cases_for_version(conn, version["id"]),
            "action": None,
            "rollback_action": None,
            "rollout": latest_rollout,
            "feedback_health": feedback_health,
            "prompt_snapshots": prompt_snapshots,
        }
    if version["status"] == "rejected":
        return {
            "stage": "Rejected",
            "kind": "rejected",
            "version": version,
            "gate_state": "rejected",
            "score": None,
            "red_flags": ["Human decision: do not ship."],
            "path_to_green": ["Create a new candidate if the idea should be revisited."],
            "use_cases": intended_use_cases_for_version(conn, version["id"]),
            "action": None,
            "rollback_action": None,
            "rollout": latest_rollout,
            "feedback_health": feedback_health,
            "prompt_snapshots": prompt_snapshots,
        }
    if version["status"] == "approved":
        return {
            "stage": "Needs Tuning",
            "kind": "needs-tuning",
            "version": version,
            "gate_state": "baseline",
            "score": None,
            "red_flags": ["Prior production version; no longer serving live traffic."],
            "path_to_green": [
                "Available as rollback history. Rerun eval if this behavior should become a new candidate."
            ],
            "use_cases": intended_use_cases_for_version(conn, version["id"]),
            "action": None,
            "rollback_action": None,
            "rollout": latest_rollout,
            "feedback_health": feedback_health,
            "prompt_snapshots": prompt_snapshots,
        }

    run_row = latest_eval_for_candidate(conn, version["id"])
    if not run_row:
        return {
            "stage": "Candidate Pool",
            "kind": "draft",
            "version": version,
            "gate_state": "not evaluated",
            "score": None,
            "red_flags": [],
            "path_to_green": ["Run eval comparison."],
            "use_cases": intended_use_cases_for_version(conn, version["id"]),
            "action": None,
            "rollback_action": None,
            "rollout": None,
            "feedback_health": feedback_health,
            "prompt_snapshots": prompt_snapshots,
        }

    run = get_eval_run(conn, run_row["id"])
    stale = is_eval_stale(conn, run_row, version)
    gate = run["gate"]
    flags = []
    if stale:
        flags.append("stale eval")
    flags.extend(gate.get("blockers", []))
    flags.extend(gate.get("warnings", []))

    action = None
    rollback_action = None

    if latest_rollout and latest_rollout["status"] in ("held", "rolled_back"):
        stage = "Blocked" if latest_rollout["status"] == "held" else "Needs Tuning"
        kind = "blocked" if latest_rollout["status"] == "held" else "needs-tuning"
        flags.append("rollout {}".format(latest_rollout["status"]))
    elif gate["state"] == "block":
        stage = "Blocked"
        kind = "blocked"
    elif stale or gate["state"] == "warn":
        stage = "Needs Tuning"
        kind = "needs-tuning"
    else:
        if latest_rollout:
            definition = rollout_definition(latest_rollout["stage"])
            stage = definition["label"] if definition else "Local A/B Simulation"
            kind = latest_rollout["stage"].replace("_", "-")
            next_def = next_rollout_definition(latest_rollout["stage"])
            if next_def and latest_rollout["guardrail_state"] == "healthy":
                action = next_def["action"]
            if latest_rollout["status"] == "active":
                rollback_action = "Hold / Rollback"
            if latest_rollout["guardrail_state"] != "healthy":
                flags.extend(latest_rollout.get("metrics", {}).get("reasons", []))
        else:
            stage = "Local A/B Simulation"
            kind = "local-ab"
            action = ROLLOUT_DEFS[0]["action"]

    return {
        "stage": stage,
        "kind": kind,
        "version": version,
        "gate_state": gate["state"],
        "score": gate.get("release_score"),
        "red_flags": flags[:3],
        "path_to_green": (path_to_green(run, stale) + rollout_path_to_green(latest_rollout))[:4],
        "use_cases": intended_use_cases_for_version(conn, version["id"]),
        "action": action,
        "rollback_action": rollback_action,
        "rollout": latest_rollout,
        "feedback_health": feedback_health,
        "prompt_snapshots": prompt_snapshots,
    }


def pipeline_board(conn, versions):
    stages = PIPELINE_STAGES
    board = {stage: [] for stage in stages}
    for version in versions:
        card = pipeline_card_for_version(conn, version)
        board.setdefault(card["stage"], []).append(card)
    return [{"stage": stage, "cards": board.get(stage, [])} for stage in stages]


def advance_pipeline(conn, candidate_id):
    candidate = get_version(conn, candidate_id)
    if candidate["status"] in ("production", "rejected"):
        raise ValueError("This version cannot advance through the candidate pipeline.")

    run_row, run, stale, gate = gate_for_candidate(conn, candidate_id)
    if gate["state"] == "block":
        audit(
            conn,
            "pipeline_advance_blocked",
            "Pipeline advance blocked for {}.".format(candidate["name"]),
            {"candidate_id": candidate_id, "blockers": gate["blockers"]},
        )
        conn.commit()
        raise ValueError("; ".join(gate["blockers"]))
    if gate["state"] == "warn":
        message = "Candidate needs tuning before rollout: {}".format(
            "; ".join(gate["warnings"])
        )
        audit(
            conn,
            "pipeline_advance_needs_tuning",
            "Pipeline advance held for {}.".format(candidate["name"]),
            {"candidate_id": candidate_id, "warnings": gate["warnings"]},
        )
        conn.commit()
        raise ValueError(message)

    latest = latest_rollout_stage(conn, candidate_id)
    if latest and latest["guardrail_state"] != "healthy":
        raise ValueError("Guardrails are not healthy. Hold or rollback before advancing.")

    next_def = next_rollout_definition(latest["stage"] if latest else None)
    if not next_def:
        raise ValueError("No further rollout stage is available.")

    metrics = simulate_rollout_metrics(conn, candidate_id, next_def["stage"])
    status = "active"
    if metrics["guardrail_state"] == "rollback_required":
        status = "held"

    now = now_iso()
    with db.transaction(conn):
        old_prod = get_production(conn)
        if next_def["stage"] == "production_100" and old_prod and old_prod["id"] != candidate_id:
            conn.execute(
                "UPDATE behavior_versions SET status = ?, rollout_percent = ?, updated_at = ? WHERE id = ?",
                ("approved", 0, now, old_prod["id"]),
            )
        conn.execute(
            """
            INSERT INTO rollout_stages (
                id, behavior_version_id, environment, stage, rollout_percent,
                holdout_version_id, status, guardrail_state, observation_window_minutes,
                metrics_json, started_at, completed_at, rollback_target_version_id, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("rollout"),
                candidate_id,
                next_def["environment"],
                next_def["stage"],
                next_def["rollout_percent"],
                metrics["holdout_version_id"],
                status,
                metrics["guardrail_state"],
                next_def["window"],
                db.dumps(metrics),
                now,
                now if next_def["stage"] in ("local_ab", "qa_ab") else None,
                metrics["holdout_version_id"],
                "",
            ),
        )
        conn.execute(
            "UPDATE behavior_versions SET rollout_percent = ?, status = ?, updated_at = ? WHERE id = ?",
            (
                next_def["rollout_percent"],
                "production" if next_def["stage"] == "production_100" else "staging",
                now,
                candidate_id,
            ),
        )
        if next_def["stage"] == "production_100":
            update_approved_prompt_registry(conn, candidate_id, run)
        audit(
            conn,
            "pipeline_stage_advanced",
            "Advanced {} to {}.".format(candidate["name"], next_def["label"]),
            {
                "candidate_id": candidate_id,
                "stage": next_def["stage"],
                "guardrail_state": metrics["guardrail_state"],
                "holdout_version_id": metrics["holdout_version_id"],
            },
        )
    return next_def["stage"]


def rollback_pipeline(conn, candidate_id, reason="Guardrail rollback."):
    candidate = get_version(conn, candidate_id)
    latest = latest_rollout_stage(conn, candidate_id)
    if not latest:
        raise ValueError("No rollout stage exists to rollback.")
    now = now_iso()
    with db.transaction(conn):
        conn.execute(
            """
            UPDATE rollout_stages
            SET status = ?, completed_at = ?, reason = ?
            WHERE id = ?
            """,
            ("rolled_back", now, reason, latest["id"]),
        )
        conn.execute(
            "UPDATE behavior_versions SET status = ?, rollout_percent = ?, updated_at = ? WHERE id = ?",
            ("evaluated", 0, now, candidate_id),
        )
        audit(
            conn,
            "pipeline_rolled_back",
            "Rolled back {} from {}.".format(candidate["name"], latest["stage"]),
            {
                "candidate_id": candidate_id,
                "stage": latest["stage"],
                "reason": reason,
                "rollback_target_version_id": latest["rollback_target_version_id"],
            },
        )


def update_approved_prompt_registry(conn, candidate_id, run):
    candidate = get_version(conn, candidate_id)
    production = get_production(conn)
    for evidence in list_evidence(conn, run["id"]):
        conn.execute(
            """
            INSERT INTO approved_prompt_registry (
                use_case, feature, approved_behavior_version_id, prompt_config_hash,
                production_version_id, eval_suite_hash, evaluator_version,
                provider_snapshot, last_passing_eval_run_id, last_rollout_stage,
                risk_level, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(use_case) DO UPDATE SET
                feature = excluded.feature,
                approved_behavior_version_id = excluded.approved_behavior_version_id,
                prompt_config_hash = excluded.prompt_config_hash,
                production_version_id = excluded.production_version_id,
                eval_suite_hash = excluded.eval_suite_hash,
                evaluator_version = excluded.evaluator_version,
                provider_snapshot = excluded.provider_snapshot,
                last_passing_eval_run_id = excluded.last_passing_eval_run_id,
                last_rollout_stage = excluded.last_rollout_stage,
                risk_level = excluded.risk_level,
                updated_at = excluded.updated_at
            """,
            (
                evidence["use_case"],
                evidence["feature"],
                candidate_id,
                candidate["config_hash"],
                production["id"] if production else candidate_id,
                run["eval_suite_hash"],
                run["evaluator_version"],
                run["provider_snapshot"],
                run["id"],
                "production_100",
                "high" if evidence["critical_regression_count"] > 0 else "medium",
                now_iso(),
            ),
        )


def rebuild_evidence_index(conn, eval_run_id):
    run = get_eval_run(conn, eval_run_id)
    if not run:
        return
    candidate_id = run["candidate_version_id"]
    weights = get_weights(conn)
    weight_hash = release_gate.feedback_config_hash(weights)
    source_hash = stable_hash(
        {
            "eval_run": eval_run_id,
            "summary": run["summary"],
            "gate": run["gate"],
            "weight_hash": weight_hash,
        }
    )
    results_by_feature = {}
    for result in run["results"]:
        key = (result["feature"], result["use_case"])
        results_by_feature.setdefault(key, []).append(result)

    with db.transaction(conn):
        conn.execute(
            "DELETE FROM release_evidence_index WHERE eval_run_id = ? AND cache_key = ?",
            (eval_run_id, weight_hash),
        )
        for (feature, use_case), results in results_by_feature.items():
            passed = sum(1 for item in results if item["candidate_passed"])
            regressions = [item for item in results if item["classification"] == "regressed"]
            critical = [item for item in regressions if item["severity"] == "critical"]
            accuracy = passed / max(1, len(results))
            conn.execute(
                """
                INSERT INTO release_evidence_index (
                    behavior_version_id, feature, use_case, eval_run_id, accuracy,
                    regression_count, critical_regression_count, feedback_score,
                    release_score, gate_state, cache_key, source_hash, status, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    feature,
                    use_case,
                    eval_run_id,
                    accuracy,
                    len(regressions),
                    len(critical),
                    run["gate"].get("feedback", {}).get("adjusted_feedback_score", 0.0),
                    run["gate"].get("release_score", 0.0),
                    run["gate"].get("state", "block"),
                    weight_hash,
                    source_hash,
                    "fresh",
                    now_iso(),
                ),
            )
            conn.execute(
                """
                UPDATE feature_use_case_map
                SET last_candidate_accuracy = ?, last_eval_run_id = ?, updated_at = ?
                WHERE feature = ? AND use_case = ?
                """,
                (accuracy, eval_run_id, now_iso(), feature, use_case),
            )


def is_eval_stale(conn, run, candidate):
    production = get_production(conn)
    return (
        run["candidate_config_hash"] != candidate["config_hash"]
        or run["production_config_hash"] != production["config_hash"]
        or run["provider_snapshot"] != mock_model.PROVIDER_SNAPSHOT
        or run["evaluator_version"] != evaluator.EVALUATOR_VERSION
    )


def promote_candidate(conn, candidate_id):
    candidate = get_version(conn, candidate_id)
    run_row = latest_eval_for_candidate(conn, candidate_id)
    if not run_row:
        raise ValueError("Run an eval comparison before promotion.")
    run = get_eval_run(conn, run_row["id"])
    stale = is_eval_stale(conn, run_row, candidate)
    partial = run_row["status"] != "completed"
    gate = release_gate.evaluate_gate(
        run["summary"],
        run["gate"].get("feedback", {}),
        comparable=True,
        stale=stale,
        partial=partial,
    )
    if gate["state"] == "block":
        audit(
            conn,
            "release_blocked",
            "Promotion blocked for {}.".format(candidate["name"]),
            {"candidate_id": candidate_id, "blockers": gate["blockers"]},
        )
        conn.commit()
        raise ValueError("; ".join(gate["blockers"]))

    with db.transaction(conn):
        old_prod = get_production(conn)
        conn.execute(
            "UPDATE behavior_versions SET status = ?, rollout_percent = ?, updated_at = ? WHERE id = ?",
            ("approved", 0, now_iso(), old_prod["id"]),
        )
        conn.execute(
            "UPDATE behavior_versions SET status = ?, rollout_percent = ?, updated_at = ? WHERE id = ?",
            ("production", 100, now_iso(), candidate_id),
        )
        audit(
            conn,
            "version_promoted",
            "Promoted {} to production.".format(candidate["name"]),
            {
                "candidate_id": candidate_id,
                "previous_production_id": old_prod["id"],
                "eval_run_id": run["id"],
                "gate_state": gate["state"],
            },
        )


def reject_candidate(conn, candidate_id, reason="Rejected by reviewer."):
    candidate = get_version(conn, candidate_id)
    if candidate["status"] == "production":
        raise ValueError("Production version cannot be rejected.")
    with db.transaction(conn):
        conn.execute(
            "UPDATE behavior_versions SET status = ?, updated_at = ? WHERE id = ?",
            ("rejected", now_iso(), candidate_id),
        )
        audit(
            conn,
            "version_rejected",
            "Rejected {}.".format(candidate["name"]),
            {"candidate_id": candidate_id, "reason": reason},
        )


def parse_iso(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def auto_reject_expired_blocked_candidates(conn, max_age_hours=24):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    versions = [
        version
        for version in list_versions(conn)
        if version["status"] not in ("production", "rejected")
    ]
    rejected = []
    with db.transaction(conn):
        for version in versions:
            latest_rollout = latest_rollout_stage(conn, version["id"])
            if latest_rollout and latest_rollout["status"] == "held":
                blocked_at = parse_iso(latest_rollout["started_at"])
                reason = "No reviewer action within {} hours after rollout hold.".format(
                    max_age_hours
                )
            else:
                run_row = latest_eval_for_candidate(conn, version["id"])
                if not run_row:
                    continue
                run = get_eval_run(conn, run_row["id"])
                if run["gate"].get("state") != "block":
                    continue
                blocked_at = parse_iso(run_row["created_at"])
                reason = "No reviewer action within {} hours after release gate block.".format(
                    max_age_hours
                )

            if blocked_at and blocked_at <= cutoff:
                conn.execute(
                    "UPDATE behavior_versions SET status = ?, updated_at = ? WHERE id = ?",
                    ("rejected", now_iso(), version["id"]),
                )
                audit(
                    conn,
                    "blocked_candidate_auto_rejected",
                    "Auto-rejected {} after unresolved block.".format(version["name"]),
                    {
                        "candidate_id": version["id"],
                        "blocked_at": blocked_at.isoformat(),
                        "max_age_hours": max_age_hours,
                        "reason": reason,
                    },
                )
                rejected.append(version["id"])
    return rejected


def dashboard_state(conn, candidate_id=None):
    auto_reject_expired_blocked_candidates(conn)
    versions = list_versions(conn)
    production = get_production(conn)
    candidates = [item for item in versions if item["status"] != "production"]
    candidate = get_version(conn, candidate_id) if candidate_id else None
    if not candidate and candidates:
        candidate = candidates[0]

    latest_eval = latest_eval_for_candidate(conn, candidate["id"]) if candidate else None
    eval_run = get_eval_run(conn, latest_eval["id"]) if latest_eval else None
    stale = is_eval_stale(conn, latest_eval, candidate) if latest_eval and candidate else True
    evidence = list_evidence(conn, eval_run["id"]) if eval_run else []

    prod_feedback = get_feedback(conn, production["id"])
    cand_feedback = get_feedback(conn, candidate["id"]) if candidate else None
    weights = get_weights(conn)
    feedback_score = (
        release_gate.score_feedback(prod_feedback, cand_feedback, weights)
        if cand_feedback
        else None
    )
    production_prompt_feedback = list_prompt_feedback(conn, production["id"])
    candidate_prompt_feedback = (
        list_prompt_feedback(conn, candidate["id"]) if candidate else []
    )
    preview_input = "I bought this 45 days ago. Can I get a refund?"
    behavior_preview = {
        "input": preview_input,
        "production_output": mock_model.generate(production, preview_input),
        "candidate_output": mock_model.generate(candidate, preview_input)
        if candidate
        else "",
    }

    return {
        "versions": versions,
        "pipeline": pipeline_board(conn, versions),
        "production": production,
        "candidate": candidate,
        "eval_run": eval_run,
        "stale": stale,
        "evidence": evidence,
        "eval_cases": list_eval_cases(conn),
        "production_feedback": prod_feedback,
        "candidate_feedback": cand_feedback,
        "production_prompt_feedback": production_prompt_feedback,
        "candidate_prompt_feedback": candidate_prompt_feedback,
        "candidate_prompt_feedback_summary": summarize_prompt_feedback(
            candidate_prompt_feedback
        ),
        "weights": weights,
        "feedback_score": feedback_score,
        "behavior_preview": behavior_preview,
        "audit_events": list_audit_events(conn),
    }
