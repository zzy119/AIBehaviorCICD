import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from behavior_release_manager import db, mock_model, seed, services, templates
from behavior_release_manager.release_gate import score_feedback


class ReleaseWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(delete=True)
        self.conn = db.connect(self.temp.name)
        db.init_db(self.conn)
        seed.seed(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.close()

    def test_risky_candidate_blocks_on_critical_regression(self):
        run_id = services.run_eval(self.conn, "cand_risky_delight")
        run = services.get_eval_run(self.conn, run_id)

        self.assertEqual(run["gate"]["state"], "block")
        self.assertEqual(run["summary"]["critical_regression_count"], 1)
        self.assertLess(
            run["summary"]["candidate_pass_rate"],
            run["summary"]["production_pass_rate"],
        )

        with self.assertRaises(ValueError):
            services.promote_candidate(self.conn, "cand_risky_delight")

    def test_safe_candidate_can_promote_and_preserves_single_production(self):
        run_id = services.run_eval(self.conn, "cand_safe_policy")
        run = services.get_eval_run(self.conn, run_id)

        self.assertEqual(run["gate"]["state"], "pass")
        services.promote_candidate(self.conn, "cand_safe_policy")

        versions = services.list_versions(self.conn)
        production_versions = [
            version for version in versions if version["status"] == "production"
        ]
        self.assertEqual(len(production_versions), 1)
        self.assertEqual(production_versions[0]["id"], "cand_safe_policy")

    def test_candidate_edit_makes_prior_eval_stale_and_blocks_promotion(self):
        services.run_eval(self.conn, "cand_safe_policy")
        services.update_candidate(
            self.conn,
            "cand_safe_policy",
            {
                "prompt": "New untested prompt.",
                "model": "mock-support-001",
                "temperature": "0.3",
                "rollout_percent": "0",
                "release_notes": "Changed after eval.",
            },
        )

        with self.assertRaises(ValueError) as error:
            services.promote_candidate(self.conn, "cand_safe_policy")
        self.assertIn("Candidate changed", str(error.exception))

        board = services.pipeline_board(self.conn, services.list_versions(self.conn))
        needs_tuning_cards = [
            card
            for column in board
            if column["stage"] == "Needs Tuning"
            for card in column["cards"]
        ]
        blocked_cards = [
            card
            for column in board
            if column["stage"] == "Blocked"
            for card in column["cards"]
        ]

        self.assertTrue(
            any(card["version"]["id"] == "cand_safe_policy" for card in needs_tuning_cards)
        )
        self.assertFalse(
            any(card["version"]["id"] == "cand_safe_policy" for card in blocked_cards)
        )

    def test_temperature_change_changes_mock_behavior_and_gate(self):
        before = services.dashboard_state(self.conn, "cand_safe_policy")[
            "behavior_preview"
        ]["candidate_output"]
        candidate = services.get_version(self.conn, "cand_safe_policy")
        services.update_candidate(
            self.conn,
            "cand_safe_policy",
            {
                "prompt": candidate["prompt"],
                "model": candidate["model"],
                "temperature": "1.2",
                "rollout_percent": str(candidate["rollout_percent"]),
                "release_notes": candidate["release_notes"],
            },
        )
        after = services.dashboard_state(self.conn, "cand_safe_policy")[
            "behavior_preview"
        ]["candidate_output"]

        self.assertNotEqual(before, after)
        self.assertIn("eligible", after)

        run_id = services.run_eval(self.conn, "cand_safe_policy")
        run = services.get_eval_run(self.conn, run_id)
        self.assertEqual(run["gate"]["state"], "block")
        self.assertGreater(run["summary"]["critical_regression_count"], 0)

    def test_seeded_configs_produce_different_refund_replies(self):
        refund_input = "I bought this 45 days ago. Can I get a refund?"
        production = services.get_version(self.conn, "prod_v1")
        safe = services.get_version(self.conn, "cand_safe_policy")
        tuning = services.get_version(self.conn, "cand_needs_tuning")

        production_reply = mock_model.generate(production, refund_input)
        safe_reply = mock_model.generate(safe, refund_input)
        tuning_reply = mock_model.generate(tuning, refund_input)

        self.assertNotEqual(production_reply, safe_reply)
        self.assertNotEqual(safe_reply, tuning_reply)
        self.assertIn("temp=strict", production_reply)
        self.assertIn("temp=warm", safe_reply)
        self.assertIn("temp=expanded", tuning_reply)

    def test_prompt_change_changes_mock_behavior_profile(self):
        before = services.dashboard_state(self.conn, "cand_safe_policy")[
            "behavior_preview"
        ]["candidate_output"]
        candidate = services.get_version(self.conn, "cand_safe_policy")
        services.update_candidate(
            self.conn,
            "cand_safe_policy",
            {
                "prompt": candidate["prompt"] + "\nUse a slightly more reassuring closing.",
                "model": candidate["model"],
                "temperature": str(candidate["temperature"]),
                "rollout_percent": str(candidate["rollout_percent"]),
                "release_notes": candidate["release_notes"],
            },
        )
        after = services.dashboard_state(self.conn, "cand_safe_policy")[
            "behavior_preview"
        ]["candidate_output"]

        self.assertNotEqual(before, after)
        self.assertIn("Mock behavior profile:", after)

    def test_candidate_config_update_creates_prompt_version_snapshot(self):
        candidate = services.get_version(self.conn, "cand_safe_policy")
        self.assertTrue(candidate["name"].endswith("v1"))

        services.update_candidate(
            self.conn,
            "cand_safe_policy",
            {
                "prompt": candidate["prompt"] + "\nUse a reassuring close.",
                "model": candidate["model"],
                "temperature": str(candidate["temperature"]),
                "rollout_percent": str(candidate["rollout_percent"]),
                "release_notes": candidate["release_notes"],
            },
        )

        updated = services.get_version(self.conn, "cand_safe_policy")
        snapshots = services.list_prompt_snapshots(self.conn, "cand_safe_policy")
        self.assertTrue(updated["name"].endswith("v2"))
        self.assertEqual(snapshots[0]["version_number"], 2)
        self.assertIn("reassuring close", snapshots[0]["prompt"])

    def test_evidence_index_is_rebuilt_after_eval(self):
        run_id = services.run_eval(self.conn, "cand_safe_policy")
        evidence = services.list_evidence(self.conn, run_id)

        self.assertGreaterEqual(len(evidence), 1)
        self.assertTrue(all(item["status"] == "fresh" for item in evidence))
        self.assertTrue(any(item["feature"] == "Refund policy" for item in evidence))

    def test_feedback_mixed_signal_penalty_catches_conflicts(self):
        prod = services.get_feedback(self.conn, "prod_v1")
        cand = services.get_feedback(self.conn, "cand_risky_delight")
        weights = services.get_weights(self.conn)

        result = score_feedback(prod, cand, weights)

        self.assertGreater(result["mixed_signal_penalty"], 0)
        self.assertTrue(
            any("escalation" in item for item in result["high_risk_worsened"])
        )

    def test_safe_candidate_advances_through_progressive_pipeline(self):
        services.run_eval(self.conn, "cand_safe_policy")

        expected = [
            "local_ab",
            "qa_ab",
            "production_10",
            "production_50",
            "production_100",
        ]
        actual = [
            services.advance_pipeline(self.conn, "cand_safe_policy")
            for _ in expected
        ]

        self.assertEqual(actual, expected)
        self.assertEqual(services.get_production(self.conn)["id"], "cand_safe_policy")
        latest = services.latest_rollout_stage(self.conn, "cand_safe_policy")
        self.assertEqual(latest["stage"], "production_100")
        self.assertEqual(latest["guardrail_state"], "healthy")

    def test_risky_candidate_cannot_enter_pipeline(self):
        services.run_eval(self.conn, "cand_risky_delight")

        with self.assertRaises(ValueError) as error:
            services.advance_pipeline(self.conn, "cand_risky_delight")
        self.assertIn("critical regression", str(error.exception))

    def test_needs_tuning_candidate_stays_out_of_rollout(self):
        run_id = services.run_eval(self.conn, "cand_needs_tuning")
        run = services.get_eval_run(self.conn, run_id)

        self.assertEqual(run["gate"]["state"], "warn")
        self.assertEqual(run["summary"]["critical_regression_count"], 0)
        self.assertEqual(
            run["summary"]["candidate_pass_rate"],
            run["summary"]["production_pass_rate"],
        )

        with self.assertRaises(ValueError) as error:
            services.advance_pipeline(self.conn, "cand_needs_tuning")
        self.assertIn("needs tuning", str(error.exception))

        html = templates.render_dashboard(
            services.dashboard_state(self.conn, "cand_needs_tuning")
        )
        self.assertIn("Needs Tuning", html)
        self.assertIn("Weighted feedback score is worse than production", html)
        self.assertNotIn("Run Local A/B", html)

    def test_pipeline_rollback_returns_candidate_to_evaluated(self):
        services.run_eval(self.conn, "cand_safe_policy")
        services.advance_pipeline(self.conn, "cand_safe_policy")
        services.advance_pipeline(self.conn, "cand_safe_policy")

        services.rollback_pipeline(self.conn, "cand_safe_policy", "Testing rollback.")

        candidate = services.get_version(self.conn, "cand_safe_policy")
        latest = services.latest_rollout_stage(self.conn, "cand_safe_policy")
        self.assertEqual(candidate["status"], "evaluated")
        self.assertEqual(candidate["rollout_percent"], 0)
        self.assertEqual(latest["status"], "rolled_back")

        board = services.pipeline_board(self.conn, services.list_versions(self.conn))
        needs_tuning_cards = [
            card
            for column in board
            if column["stage"] == "Needs Tuning"
            for card in column["cards"]
        ]
        self.assertTrue(
            any(card["version"]["id"] == "cand_safe_policy" for card in needs_tuning_cards)
        )

    def test_dashboard_renders_release_pipeline_guidance(self):
        services.run_eval(self.conn, "cand_safe_policy")
        services.advance_pipeline(self.conn, "cand_safe_policy")

        html = templates.render_dashboard(
            services.dashboard_state(self.conn, "cand_safe_policy")
        )

        self.assertIn("release-map", html)
        self.assertIn("Production baseline", html)
        self.assertIn("Candidate draft", html)
        self.assertIn("prod_v1", html)
        self.assertIn("cand_safe_policy", html)
        self.assertIn("Pipeline rollout", html)
        self.assertIn("Prompt Reply Comparison", html)
        self.assertIn("Production baseline reply", html)
        self.assertIn("Candidate draft reply", html)
        self.assertIn("Eval Gate", html)
        self.assertNotIn('<div class="panel-title">Release Gate</div>', html)
        self.assertIn("pipeline-board-primary", html)
        self.assertIn("pipeline-board-decisions", html)
        self.assertIn("Decision and follow-up lanes", html)
        self.assertIn("Local A/B", html)
        self.assertIn("QA / A-B", html)
        self.assertIn("QA / A-B Test", html)
        self.assertIn("rollout-facts", html)
        self.assertIn("Hold / Rollback", html)
        self.assertIn("Prompt Feedback Store", html)
        self.assertIn("Candidate feedback", html)
        self.assertIn("Suggested prompt change", html)
        self.assertIn("Production rollout events", html)
        self.assertIn("Local proxy events", html)
        self.assertIn("Production feedback healthy", html)
        self.assertIn("Prompt details", html)
        self.assertIn("Prompt version history", html)
        self.assertIn("usecase-hover", html)
        self.assertIn("usecase-popover", html)
        self.assertIn('<details class="audit-context">', html)
        self.assertIn("<summary>Details</summary>", html)
        self.assertIn("Prompt snapshot", html)
        self.assertIn("Eval run", html)
        self.assertIn("Gate", html)
        self.assertIn("Release score", html)
        self.assertIn("Config hash", html)

    def test_prompt_feedback_events_are_stored_for_candidate_improvement(self):
        events = services.list_prompt_feedback(self.conn, "cand_needs_tuning")

        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["sentiment"], "negative")
        self.assertEqual(events[0]["source_type"], "local proxy with red flag")
        self.assertEqual(events[0]["source_weight"], 0.75)
        self.assertIn("Move the 30-day boundary", events[0]["suggested_prompt_change"])

        production_events = services.list_prompt_feedback(self.conn, "cand_safe_policy")
        self.assertTrue(
            any(event["source_type"] == "production rollout" for event in production_events)
        )

    def test_pipeline_feedback_health_uses_production_signals(self):
        baseline_health = services.feedback_health_for_version(self.conn, "prod_v1")
        safe_health = services.feedback_health_for_version(self.conn, "cand_safe_policy")
        tuning_health = services.feedback_health_for_version(
            self.conn, "cand_needs_tuning"
        )
        risky_health = services.feedback_health_for_version(
            self.conn, "cand_risky_delight"
        )

        self.assertEqual(baseline_health["state"], "neutral")
        self.assertEqual(baseline_health["label"], "Production feedback observed")
        self.assertEqual(safe_health["state"], "pass")
        self.assertEqual(tuning_health["state"], "warn")
        self.assertEqual(risky_health["state"], "block")

    def test_production_pipeline_card_is_baseline_not_auto_pass(self):
        board = services.pipeline_board(self.conn, services.list_versions(self.conn))
        production_cards = [
            card
            for column in board
            if column["stage"] == "Production"
            for card in column["cards"]
        ]

        self.assertTrue(any(card["version"]["id"] == "prod_v1" for card in production_cards))
        prod_card = next(card for card in production_cards if card["version"]["id"] == "prod_v1")
        self.assertEqual(prod_card["gate_state"], "baseline")
        self.assertEqual(prod_card["feedback_health"]["label"], "Production feedback observed")

    def test_gate_status_uses_traffic_light_classes(self):
        services.run_eval(self.conn, "cand_safe_policy")
        pass_html = templates.render_dashboard(
            services.dashboard_state(self.conn, "cand_safe_policy")
        )
        self.assertIn("metric-pass", pass_html)
        self.assertIn("badge-pass", pass_html)

        services.run_eval(self.conn, "cand_needs_tuning")
        warn_html = templates.render_dashboard(
            services.dashboard_state(self.conn, "cand_needs_tuning")
        )
        self.assertIn("metric-warn", warn_html)
        self.assertIn("badge-warn", warn_html)

        services.run_eval(self.conn, "cand_risky_delight")
        block_html = templates.render_dashboard(
            services.dashboard_state(self.conn, "cand_risky_delight")
        )
        self.assertIn("metric-block", block_html)
        self.assertIn("badge-block", block_html)

    def test_blocked_candidate_auto_rejects_after_one_day_without_action(self):
        run_id = services.run_eval(self.conn, "cand_risky_delight")
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).replace(
            microsecond=0
        )
        self.conn.execute(
            "UPDATE eval_runs SET created_at = ? WHERE id = ?",
            (old_time.isoformat(), run_id),
        )
        self.conn.commit()

        rejected = services.auto_reject_expired_blocked_candidates(self.conn)
        version = services.get_version(self.conn, "cand_risky_delight")
        audit_events = services.list_audit_events(self.conn)

        self.assertIn("cand_risky_delight", rejected)
        self.assertEqual(version["status"], "rejected")
        self.assertTrue(
            any(event["event_type"] == "blocked_candidate_auto_rejected" for event in audit_events)
        )

    def test_stale_warning_candidate_does_not_auto_reject_after_one_day(self):
        run_id = services.run_eval(self.conn, "cand_safe_policy")
        candidate = services.get_version(self.conn, "cand_safe_policy")
        services.update_candidate(
            self.conn,
            "cand_safe_policy",
            {
                "prompt": "New untested prompt.",
                "model": candidate["model"],
                "temperature": str(candidate["temperature"]),
                "rollout_percent": str(candidate["rollout_percent"]),
                "release_notes": candidate["release_notes"],
            },
        )
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).replace(
            microsecond=0
        )
        self.conn.execute(
            "UPDATE eval_runs SET created_at = ? WHERE id = ?",
            (old_time.isoformat(), run_id),
        )
        self.conn.commit()

        rejected = services.auto_reject_expired_blocked_candidates(self.conn)
        version = services.get_version(self.conn, "cand_safe_policy")

        self.assertNotIn("cand_safe_policy", rejected)
        self.assertNotEqual(version["status"], "rejected")

    def test_decision_lanes_show_block_policy_and_no_duplicate_card_metadata(self):
        html = templates.render_dashboard(services.dashboard_state(self.conn))

        self.assertIn("decision-lane-warn", html)
        self.assertIn("decision-lane-block", html)
        self.assertIn(">Block</strong>", html)
        self.assertIn("Auto-rejects after 1 day without reviewer action.", html)
        self.assertIn("If no action is taken within 1 day", html)
        self.assertNotIn("Needs Tuning | cand_needs_tuning", html)
        self.assertNotIn("Blocked | cand_risky_delight", html)
        self.assertNotIn("Candidate Pool | cand_safe_policy", html)

    def test_prior_production_label_is_distinct_from_active_production(self):
        services.run_eval(self.conn, "cand_safe_policy")
        for _ in range(5):
            services.advance_pipeline(self.conn, "cand_safe_policy")

        board = services.pipeline_board(self.conn, services.list_versions(self.conn))
        candidate_pool_ids = [
            card["version"]["id"]
            for column in board
            if column["stage"] == "Candidate Pool"
            for card in column["cards"]
        ]
        needs_tuning_ids = [
            card["version"]["id"]
            for column in board
            if column["stage"] == "Needs Tuning"
            for card in column["cards"]
        ]
        html = templates.render_dashboard(services.dashboard_state(self.conn, "prod_v1"))

        self.assertNotIn("prod_v1", candidate_pool_ids)
        self.assertIn("prod_v1", needs_tuning_ids)
        self.assertIn("Prior Production - Support Policy v1", html)
        self.assertIn("Candidate - Warm Policy Guardrails v1", html)

    def test_reset_demo_returns_candidate_to_local_ab_path(self):
        services.run_eval(self.conn, "cand_safe_policy")
        for _ in range(5):
            services.advance_pipeline(self.conn, "cand_safe_policy")
        self.assertEqual(services.get_production(self.conn)["id"], "cand_safe_policy")

        services.reset_demo_data(self.conn)
        seed.seed(self.conn)

        self.assertEqual(services.get_production(self.conn)["id"], "prod_v1")
        run_id = services.run_eval(self.conn, "cand_safe_policy")
        run = services.get_eval_run(self.conn, run_id)
        self.assertEqual(run["gate"]["state"], "pass")
        board = services.pipeline_board(self.conn, services.list_versions(self.conn))
        local_ab_cards = [
            card
            for column in board
            if column["stage"] == "Local A/B Simulation"
            for card in column["cards"]
        ]
        self.assertTrue(
            any(card["version"]["id"] == "cand_safe_policy" for card in local_ab_cards)
        )


if __name__ == "__main__":
    unittest.main()
