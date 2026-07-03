import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = APP_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "behavior_release_manager.sqlite3"


def get_db_path():
    return Path(os.environ.get("BRM_DB_PATH", DEFAULT_DB_PATH))


def connect(db_path=None):
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn):
    try:
        conn.execute("BEGIN")
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def loads(value, default=None):
    if value is None:
        return default
    return json.loads(value)


def row_to_dict(row):
    return dict(row) if row is not None else None


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS behavior_versions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            prompt TEXT NOT NULL,
            model TEXT NOT NULL,
            temperature REAL NOT NULL,
            status TEXT NOT NULL,
            rollout_percent INTEGER NOT NULL DEFAULT 0,
            release_notes TEXT NOT NULL DEFAULT '',
            config_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS prompt_config_snapshots (
            id TEXT PRIMARY KEY,
            behavior_version_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            prompt TEXT NOT NULL,
            model TEXT NOT NULL,
            temperature REAL NOT NULL,
            config_hash TEXT NOT NULL,
            change_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(behavior_version_id, version_number),
            FOREIGN KEY(behavior_version_id) REFERENCES behavior_versions(id)
        );

        CREATE TABLE IF NOT EXISTS eval_cases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            input TEXT NOT NULL,
            expected_behavior TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            severity TEXT NOT NULL,
            must_include_json TEXT NOT NULL,
            must_not_include_json TEXT NOT NULL,
            feature TEXT NOT NULL,
            use_case TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feature_use_case_map (
            id TEXT PRIMARY KEY,
            feature TEXT NOT NULL,
            use_case TEXT NOT NULL,
            owner TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL,
            eval_case_ids_json TEXT NOT NULL,
            target_accuracy REAL NOT NULL,
            last_production_accuracy REAL,
            last_candidate_accuracy REAL,
            last_eval_run_id TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS eval_runs (
            id TEXT PRIMARY KEY,
            production_version_id TEXT NOT NULL,
            candidate_version_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            provider_mode TEXT NOT NULL,
            provider_snapshot TEXT NOT NULL,
            evaluator_version TEXT NOT NULL,
            eval_suite_hash TEXT NOT NULL,
            production_config_hash TEXT NOT NULL,
            candidate_config_hash TEXT NOT NULL,
            run_config_hash TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            gate_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(production_version_id) REFERENCES behavior_versions(id),
            FOREIGN KEY(candidate_version_id) REFERENCES behavior_versions(id)
        );

        CREATE TABLE IF NOT EXISTS eval_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eval_run_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            production_output TEXT NOT NULL,
            candidate_output TEXT NOT NULL,
            production_score REAL NOT NULL,
            candidate_score REAL NOT NULL,
            production_passed INTEGER NOT NULL,
            candidate_passed INTEGER NOT NULL,
            classification TEXT NOT NULL,
            reason TEXT NOT NULL,
            feature TEXT NOT NULL,
            use_case TEXT NOT NULL,
            severity TEXT NOT NULL,
            FOREIGN KEY(eval_run_id) REFERENCES eval_runs(id),
            FOREIGN KEY(case_id) REFERENCES eval_cases(id)
        );

        CREATE TABLE IF NOT EXISTS feedback_signals (
            behavior_version_id TEXT PRIMARY KEY,
            thumbs_up_rate REAL NOT NULL,
            thumbs_down_rate REAL NOT NULL,
            escalation_rate REAL NOT NULL,
            retry_rate REAL NOT NULL,
            complaint_tags_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(behavior_version_id) REFERENCES behavior_versions(id)
        );

        CREATE TABLE IF NOT EXISTS prompt_feedback_events (
            id TEXT PRIMARY KEY,
            behavior_version_id TEXT NOT NULL,
            environment TEXT NOT NULL,
            rollout_stage TEXT NOT NULL,
            traffic_percent INTEGER NOT NULL,
            source TEXT NOT NULL,
            sentiment TEXT NOT NULL,
            feature TEXT NOT NULL,
            use_case TEXT NOT NULL,
            user_input TEXT NOT NULL,
            model_output TEXT NOT NULL,
            feedback_text TEXT NOT NULL,
            suggested_prompt_change TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(behavior_version_id) REFERENCES behavior_versions(id)
        );

        CREATE TABLE IF NOT EXISTS feedback_weight_configs (
            id TEXT PRIMARY KEY,
            thumbs_up_rate REAL NOT NULL,
            thumbs_down_rate REAL NOT NULL,
            escalation_rate REAL NOT NULL,
            retry_rate REAL NOT NULL,
            complaint_tags REAL NOT NULL,
            conflict_penalty REAL NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS release_evidence_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            behavior_version_id TEXT NOT NULL,
            feature TEXT NOT NULL,
            use_case TEXT NOT NULL,
            eval_run_id TEXT NOT NULL,
            accuracy REAL NOT NULL,
            regression_count INTEGER NOT NULL,
            critical_regression_count INTEGER NOT NULL,
            feedback_score REAL NOT NULL,
            release_score REAL NOT NULL,
            gate_state TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            UNIQUE(behavior_version_id, feature, use_case, eval_run_id, cache_key)
        );

        CREATE TABLE IF NOT EXISTS rollout_stages (
            id TEXT PRIMARY KEY,
            behavior_version_id TEXT NOT NULL,
            environment TEXT NOT NULL,
            stage TEXT NOT NULL,
            rollout_percent INTEGER NOT NULL,
            holdout_version_id TEXT NOT NULL,
            status TEXT NOT NULL,
            guardrail_state TEXT NOT NULL,
            observation_window_minutes INTEGER NOT NULL,
            metrics_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            rollback_target_version_id TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(behavior_version_id) REFERENCES behavior_versions(id),
            FOREIGN KEY(holdout_version_id) REFERENCES behavior_versions(id),
            FOREIGN KEY(rollback_target_version_id) REFERENCES behavior_versions(id)
        );

        CREATE TABLE IF NOT EXISTS approved_prompt_registry (
            use_case TEXT PRIMARY KEY,
            feature TEXT NOT NULL,
            approved_behavior_version_id TEXT NOT NULL,
            prompt_config_hash TEXT NOT NULL,
            production_version_id TEXT NOT NULL,
            eval_suite_hash TEXT NOT NULL,
            evaluator_version TEXT NOT NULL,
            provider_snapshot TEXT NOT NULL,
            last_passing_eval_run_id TEXT NOT NULL,
            last_rollout_stage TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(approved_behavior_version_id) REFERENCES behavior_versions(id),
            FOREIGN KEY(production_version_id) REFERENCES behavior_versions(id)
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
