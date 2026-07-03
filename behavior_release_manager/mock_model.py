PROVIDER_MODE = "mock"
PROVIDER_SNAPSHOT = "mock-support-provider-v1"

from .utils import stable_hash


STYLE_PROFILES = [
    "concise-policy",
    "warm-policy",
    "formal-handoff",
    "empathetic-brief",
    "direct-support",
    "careful-guardrail",
]


def behavior_profile(behavior_version):
    temperature = float(behavior_version["temperature"])
    signature = stable_hash(
        {
            "prompt": behavior_version["prompt"],
            "model": behavior_version["model"],
            "temperature": round(temperature, 2),
        }
    )
    style = STYLE_PROFILES[int(signature[:2], 16) % len(STYLE_PROFILES)]
    return {
        "signature": signature[:6],
        "style": style,
        "temperature": temperature,
        "temperature_band": temperature_band(temperature),
        "creative_mode": temperature >= 1.0,
    }


def temperature_band(temperature):
    if temperature < 0.25:
        return "strict"
    if temperature < 0.4:
        return "warm"
    if temperature < 0.7:
        return "expanded"
    if temperature < 1.0:
        return "exploratory"
    return "creative"


def with_profile(reply, profile):
    return "{}\n\nMock behavior profile: {} / temp={} / {}".format(
        reply,
        profile["style"],
        profile["temperature_band"],
        profile["signature"],
    )


def refund_outside_policy_reply(profile):
    if profile["temperature_band"] == "strict":
        return (
            "- Refunds are available for 30 days after purchase.\n"
            "- This request appears outside that window.\n"
            "- I can route this to support for review."
        )
    if profile["temperature_band"] == "warm":
        return (
            "- I am sorry this is not the answer you hoped for.\n"
            "- Refunds are available for 30 days after purchase.\n"
            "- Since this request is outside that window, I can route it to support for review."
        )
    if profile["temperature_band"] == "expanded":
        return (
            "- I can help you understand the next step.\n"
            "- The refund window is 30 days after purchase, and this request appears outside that policy.\n"
            "- I can send the context to support for review, but I should not promise a refund."
        )
    return (
        "- I want to help, while staying inside policy.\n"
        "- Refunds are available for 30 days after purchase.\n"
        "- This request is outside the window, so support can review context without a refund promise."
    )


def refund_inside_policy_reply(profile):
    if profile["temperature_band"] == "strict":
        return (
            "You are within the 30 days refund window and eligible for review. "
            "I can connect you with support for the next step."
        )
    if profile["temperature_band"] == "warm":
        return (
            "You are within the 30 days refund window, so this is eligible for review. "
            "I can help route this to support for the next step."
        )
    return (
        "Good news: the purchase is within the 30 days refund window and eligible for review. "
        "Support can help complete the next step."
    )


def refund_rules_reply(profile):
    if profile["temperature_band"] == "strict":
        return (
            "- Refunds are available for 30 days.\n"
            "- Support can review edge cases.\n"
            "- Guaranteed refund promises are not allowed."
        )
    if profile["temperature_band"] == "warm":
        return (
            "- Refunds are available for 30 days.\n"
            "- I can help route edge cases to support.\n"
            "- I should not promise a guaranteed refund."
        )
    return (
        "- The refund window is 30 days.\n"
        "- Support can review context for edge cases.\n"
        "- The answer should stay clear and avoid guaranteed refund promises."
    )


def frustrated_user_reply(profile, warm):
    if profile["temperature_band"] == "strict" and not warm:
        return "Please provide more details so support can review."
    if profile["temperature_band"] == "strict":
        return "I'm sorry this has taken multiple tries. I can help and route this to support."
    if profile["temperature_band"] == "warm":
        return "I'm sorry this has taken multiple tries. I can help and review the next best step with you."
    return "I'm sorry this has been frustrating. I can help summarize the issue and send it to support for review."


def generate(behavior_version, user_input):
    prompt = behavior_version["prompt"].lower()
    text = user_input.lower()
    temperature = float(behavior_version["temperature"])
    profile = behavior_profile(behavior_version)
    strict_policy = "30 days" in prompt and (
        "policy must win" in prompt or "follow policy" in prompt
    )
    delight_first = "customer delight" in prompt or "generous" in prompt
    bullet_policy = "bullet" in prompt or "concise" in prompt
    warm = "warm" in prompt or "sorry" in prompt or "delight" in prompt
    creative_mode = profile["creative_mode"]

    if "45 days" in text or "45" in text:
        if creative_mode:
            return with_profile(
                (
                    "I want to make this easy for you. You may still be eligible, "
                    "so I can look for an exception and help start the refund process."
                ),
                profile,
            )
        if strict_policy:
            return with_profile(refund_outside_policy_reply(profile), profile)
        if delight_first:
            return with_profile(
                (
                    "You may still be eligible, and I want to help. "
                    "I can start a refund right away or find a generous exception."
                ),
                profile,
            )
        return with_profile("Refund policy depends on timing. Contact support.", profile)

    if "12 days" in text:
        if creative_mode:
            return with_profile(
                (
                    "Great news: this sounds eligible, and I can help make the refund feel simple. "
                    "Support can take the next step with you."
                ),
                profile,
            )
        return with_profile(refund_inside_policy_reply(profile), profile)

    if "third time" in text or "wasting my time" in text:
        if creative_mode:
            return with_profile(
                "I'm really sorry. I can help personally and make this right with the fastest next step.",
                profile,
            )
        return with_profile(frustrated_user_reply(profile, warm or delight_first), profile)

    if "refund rules" in text:
        if creative_mode:
            return with_profile(
                "Refunds are usually 30 days, but support can be flexible and help with exceptions.",
                profile,
            )
        if bullet_policy:
            return with_profile(refund_rules_reply(profile), profile)
        return with_profile(
            "Refunds are available for 30 days. Contact support for more information.",
            profile,
        )

    if "diagnose" in text or "injury" in text:
        return with_profile(
            (
                "I cannot diagnose medical conditions from a video. "
                "Please consult a qualified professional for medical advice."
            ),
            profile,
        )

    if "human" in text or "exception" in text:
        return with_profile(
            "I can send this to support for human review and share the relevant context.",
            profile,
        )

    return with_profile("I can help with that and route to support if needed.", profile)
