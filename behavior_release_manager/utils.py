import hashlib
import json
import uuid
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def new_id(prefix):
    return "{}_{}".format(prefix, uuid.uuid4().hex[:10])


def clamp(value, low, high):
    return max(low, min(high, value))


def percent(value):
    return round(value * 100, 1)

