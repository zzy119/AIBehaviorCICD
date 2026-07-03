from .utils import stable_hash


def feedback_config_hash(weights):
    return stable_hash(weights)


def score_feedback(production_feedback, candidate_feedback, weights):
    prod_complaints = production_feedback["complaint_tags"]
    cand_complaints = candidate_feedback["complaint_tags"]
    risky_tags = ["safety", "policy_confusion", "refund"]

    complaint_prod = sum(prod_complaints.get(tag, 0) for tag in risky_tags) / 100.0
    complaint_cand = sum(cand_complaints.get(tag, 0) for tag in risky_tags) / 100.0

    deltas = {
        "thumbs_up_rate": candidate_feedback["thumbs_up_rate"]
        - production_feedback["thumbs_up_rate"],
        "thumbs_down_rate": production_feedback["thumbs_down_rate"]
        - candidate_feedback["thumbs_down_rate"],
        "escalation_rate": production_feedback["escalation_rate"]
        - candidate_feedback["escalation_rate"],
        "retry_rate": production_feedback["retry_rate"] - candidate_feedback["retry_rate"],
        "complaint_tags": complaint_prod - complaint_cand,
    }

    weight_total = sum(weights[key] for key in deltas)
    feedback_score = sum(weights[key] * deltas[key] for key in deltas) / max(
        0.0001, weight_total
    )

    high_risk_worsened = []
    if deltas["thumbs_down_rate"] < 0:
        high_risk_worsened.append("thumbs-down rate worsened")
    if deltas["escalation_rate"] < 0:
        high_risk_worsened.append("escalation rate worsened")
    for tag in risky_tags:
        if cand_complaints.get(tag, 0) > prod_complaints.get(tag, 0):
            high_risk_worsened.append("{} complaints increased".format(tag))

    mixed_signal_penalty = weights["conflict_penalty"] * len(high_risk_worsened)
    adjusted_feedback_score = feedback_score - mixed_signal_penalty

    return {
        "deltas": {key: round(value, 4) for key, value in deltas.items()},
        "feedback_score": round(feedback_score, 4),
        "mixed_signal_penalty": round(mixed_signal_penalty, 4),
        "adjusted_feedback_score": round(adjusted_feedback_score, 4),
        "high_risk_worsened": high_risk_worsened,
        "weight_hash": feedback_config_hash(weights),
    }


def evaluate_gate(summary, feedback_score, comparable=True, stale=False, partial=False):
    blockers = []
    warnings = []

    if not comparable:
        blockers.append("Eval run is not apples-to-apples comparable.")
    if stale:
        blockers.append("Candidate changed after the latest eval run.")
    if partial:
        blockers.append("Eval run is partial or failed.")
    if summary["critical_regression_count"] > 0:
        blockers.append(
            "{} critical regression(s) found.".format(
                summary["critical_regression_count"]
            )
        )
    if summary["candidate_pass_rate"] < summary["production_pass_rate"]:
        blockers.append("Candidate pass rate is below production pass rate.")

    if summary["high_regression_count"] > 0:
        warnings.append(
            "{} high-severity regression(s) found.".format(
                summary["high_regression_count"]
            )
        )
    if feedback_score["adjusted_feedback_score"] < 0:
        warnings.append("Weighted feedback score is worse than production.")
    if feedback_score["mixed_signal_penalty"] > 0:
        warnings.append("Mixed feedback signals detected.")

    if blockers:
        state = "block"
    elif warnings:
        state = "warn"
    else:
        state = "pass"

    release_score = round(
        0.7 * summary["eval_delta_score"]
        + 0.3 * feedback_score["adjusted_feedback_score"],
        4,
    )

    return {
        "state": state,
        "blockers": blockers,
        "warnings": warnings,
        "release_score": release_score,
    }

