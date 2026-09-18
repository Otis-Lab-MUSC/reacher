"""Redaction of sensitive values before they reach the log sink.

Per the project decision, field *values* are logged verbatim so that a bug can
be reproduced from the log alone — subject IDs, doses, durations and file paths
are exactly what makes a report actionable.  Credentials are the sole exception:
anything whose key looks like a secret is replaced with ``REDACTED``.

Redaction is applied on the ingest path (never trusting the browser to have done
it) and to the environment snapshot written into ``meta.json``.
"""

import re
from typing import Any

REDACTED = "[redacted]"

#: Keys whose values are replaced wholesale.  Matched case-insensitively against
#: the *key*, as a substring, so ``REACHER_API_KEY``/``apiKey``/``ws_token`` all hit.
#: Matched against a *normalised* key (lowercased, with spaces/hyphens/
#: underscores removed) so "Pairing Code", "pairing_code" and "pairingCode" all
#: hit the same rule.
_SECRET_TERMS = (
    "apikey",
    "secret",
    "password",
    "passwd",
    "token",
    "bearer",
    "authorization",
    "credential",
    "pairingcode",
    "privatekey",
)

_NORMALISE_RE = re.compile(r"[\s_-]+")

#: Maximum length of any single logged string value.  Long strings are truncated
#: rather than dropped so the shape of the data is still visible.
MAX_STR = 2048

#: Maximum number of items kept from a list/tuple.
MAX_SEQ = 100

#: Maximum nesting depth walked; deeper structures collapse to a placeholder.
MAX_DEPTH = 6


def is_secret_key(key: str) -> bool:
    """Return True if *key* names a value that must never be written to disk."""
    normalised = _NORMALISE_RE.sub("", key.lower())
    return any(term in normalised for term in _SECRET_TERMS)


#: Keys a UI record uses to describe *which* field a `value` came from.  A DOM
#: value is always logged under the generic key ``value``, so the key denylist
#: cannot see it; the field's identity has to be consulted instead.
_FIELD_IDENTITY_KEYS = ("field", "name", "label", "id", "logId", "placeholder")


def redact_ui_field(data: dict) -> dict:
    """Redact ``value`` when the record's own field identity looks secret.

    Defence in depth: the browser already does this, but the server must not
    depend on the browser having done it.
    """
    if not isinstance(data, dict) or "value" not in data:
        return data
    identity = " ".join(
        str(data[k]) for k in _FIELD_IDENTITY_KEYS if isinstance(data.get(k), (str, int))
    )
    if identity and is_secret_key(identity):
        data = dict(data)
        data["value"] = REDACTED
    return data


def redact(value: Any, _depth: int = 0) -> Any:
    """Return *value* with secrets removed and unbounded structures clamped.

    Always returns something JSON-serializable: unknown objects degrade to their
    ``repr`` rather than raising, because a logging path must never be the thing
    that breaks a request.
    """
    if _depth > MAX_DEPTH:
        return "[max-depth]"

    if value is None or isinstance(value, (bool, int)):
        return value

    if isinstance(value, float):
        # NaN/Inf are not valid JSON; keep them representable instead of failing.
        return value if -1e308 < value < 1e308 else str(value)

    if isinstance(value, str):
        text = value
        # Cheap containment check before the regex passes in scrub_message —
        # field values are logged verbatim by design and this is a hot path,
        # so every string should not pay for two regex scans on the offchance
        # it holds a credential (Fix: F5, redact() string-value gap).
        if "=" in text or "earer" in text:
            text = scrub_message(text)
        return text if len(text) <= MAX_STR else text[:MAX_STR] + f"…[+{len(text) - MAX_STR}]"

    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            ks = str(k)
            out[ks] = REDACTED if is_secret_key(ks) else redact(v, _depth + 1)
        return out

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        clamped = [redact(v, _depth + 1) for v in items[:MAX_SEQ]]
        if len(items) > MAX_SEQ:
            clamped.append(f"[+{len(items) - MAX_SEQ} more]")
        return clamped

    if isinstance(value, (bytes, bytearray)):
        return f"[{len(value)} bytes]"

    try:
        return redact(vars(value), _depth + 1)
    except TypeError:
        pass

    try:
        return str(value)[:MAX_STR]
    except Exception:
        return "[unrepresentable]"


#: Patterns for `scrub_message`.  Deliberately tight — key=value pairs and
#: Bearer tokens only, not a blanket string filter.  The project's decision
#: (see module docstring) is that field *values* stay verbatim; this only
#: catches a credential that got interpolated into a *message string*, where
#: `redact()`'s key-based matching has nothing to match against.
_MSG_SCRUB_PATTERNS = (
    (
        re.compile(r"(?i)\b(token|api[_-]?key|access[_-]?key|secret|password)=([^&\s\"'\]]+)"),
        r"\1=[redacted]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
)


def scrub_message(text: str) -> str:
    """Scrub credential-shaped substrings out of a free-text log message.

    A value interpolated into a message string (``f"... url=... {exc}"``) has
    no key for `redact()` to match — this is the backstop for that case, e.g.
    a `websockets.InvalidURI` whose ``str()``/traceback embeds the full
    connect URI, query string included (Fix: F5).
    """
    if not text:
        return text
    for pattern, repl in _MSG_SCRUB_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_env(env: dict) -> dict:
    """Return a copy of *env* limited to REACHER_* keys, with secrets removed.

    Only the project's own variables are captured — a full environment dump
    would sweep up unrelated credentials from the user's shell.
    """
    return {
        k: (REDACTED if is_secret_key(k) else v)
        for k, v in sorted(env.items())
        if k.startswith("REACHER_")
    }
