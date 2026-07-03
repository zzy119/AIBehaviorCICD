from .utils import clamp


EVALUATOR_VERSION = "deterministic-evaluator-v1"

SEVERITY_WEIGHT = {
    "low": 1,
    "medium": 2,
    "high": 4,
    "critical": 8,
}


def evaluate_output(output, eval_case):
    text = output.lower()
    must_include = eval_case["must_include"]
    must_not_include = eval_case["must_not_include"]

    included = [phrase for phrase in must_include if phrase.lower() in text]
    missing = [phrase for phrase in must_include if phrase.lower() not in text]
    violations = [phrase for phrase in must_not_include if phrase.lower() in text]

    include_score = len(included) / max(1, len(must_include))
    violation_penalty = len(violations) / max(1, len(must_not_include))
    score = clamp(include_score - violation_penalty, 0.0, 1.0)
    passed = len(missing) == 0 and len(violations) == 0

    reasons = []
    if missing:
        reasons.append("missing required phrase(s): {}".format(", ".join(missing)))
    if violations:
        reasons.append("included forbidden phrase(s): {}".format(", ".join(violations)))
    if not reasons:
        reasons.append("matched required phrases and avoided forbidden phrases")

    return {
        "score": round(score, 3),
        "passed": passed,
        "missing": missing,
        "violations": violations,
        "reason": "; ".join(reasons),
    }


def classify_result(production_eval, candidate_eval):
    if production_eval["passed"] and not candidate_eval["passed"]:
        return "regressed"
    if not production_eval["passed"] and candidate_eval["passed"]:
        return "improved"
    if production_eval["passed"] and candidate_eval["passed"]:
        if candidate_eval["score"] > production_eval["score"]:
            return "improved"
        return "unchanged_pass"
    if candidate_eval["score"] > production_eval["score"]:
        return "improved"
    if candidate_eval["score"] < production_eval["score"]:
        return "regressed"
    return "unchanged_fail"


def summarize_results(results):
    total = len(results)
    candidate_passes = sum(1 for item in results if item["candidate_passed"])
    production_passes = sum(1 for item in results if item["production_passed"])
    regressions = [item for item in results if item["classification"] == "regressed"]
    improvements = [item for item in results if item["classification"] == "improved"]
    critical_regressions = [
        item for item in regressions if item["severity"] == "critical"
    ]
    high_regressions = [item for item in regressions if item["severity"] == "high"]

    severity_total = 0.0
    weighted_delta = 0.0
    for item in results:
        weight = SEVERITY_WEIGHT[item["severity"]]
        severity_total += weight
        weighted_delta += weight * (item["candidate_score"] - item["production_score"])

    eval_delta_score = weighted_delta / max(1.0, severity_total)

    return {
        "total": total,
        "candidate_passes": candidate_passes,
        "production_passes": production_passes,
        "candidate_pass_rate": candidate_passes / max(1, total),
        "production_pass_rate": production_passes / max(1, total),
        "regression_count": len(regressions),
        "improvement_count": len(improvements),
        "critical_regression_count": len(critical_regressions),
        "high_regression_count": len(high_regressions),
        "eval_delta_score": round(eval_delta_score, 4),
    }

