from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Depends, Header, Request

from app.core.config import Settings, get_settings
from app.core.exceptions import Forbidden, RateLimited, Unauthorized


class Role(StrEnum):
    VIEWER = "viewer"
    AUTHOR = "author"  # create/update experiments
    APPROVER = "approver"  # approve experiment versions
    OPERATOR = "operator"  # start/cancel runs, retry cleanup, inspect faults
    ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    user: str
    roles: frozenset[str]

    def has(self, *roles: Role) -> bool:
        return Role.ADMIN in self.roles or any(r in self.roles for r in roles)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def authenticate(key: str | None, settings: Settings) -> Principal:
    if not key:
        raise Unauthorized("Missing X-API-Key header")
    digest = hash_key(key)
    match = None
    for known, entry in settings.api_keys.items():  # compare against every entry, constant-time each
        if hmac.compare_digest(known.lower(), digest):
            match = entry
    if match is None:
        raise Unauthorized("Invalid API key")
    return Principal(user=match.user, roles=frozenset(match.roles))


_windows: dict[str, deque[float]] = defaultdict(deque)
_redis_client = None


def _get_redis(redis_url: str):
    global _redis_client
    if _redis_client is None and redis_url:
        try:
            from redis import Redis
            _redis_client = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        except Exception:
            _redis_client = False
    return _redis_client if _redis_client is not False else None


def _rate_limit_redis(user: str, limit: int, redis_url: str) -> bool:
    r = _get_redis(redis_url)
    if not r:
        return False
    try:
        now = time.time()
        key = f"ratelimit:{user}"
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, now - 60)
        pipe.zcard(key)
        pipe.zadd(key, {f"{now}_{time.monotonic()}": now})
        pipe.expire(key, 65)
        res = pipe.execute()
        count = res[1]
        if count >= limit:
            raise RateLimited("Rate limit exceeded")
        return True
    except RateLimited:
        raise
    except Exception:
        return False


def _rate_limit_memory(user: str, limit: int) -> None:
    now = time.monotonic()
    w = _windows[user]
    while w and now - w[0] > 60:
        w.popleft()
    if len(w) >= limit:
        raise RateLimited("Rate limit exceeded")
    w.append(now)
    if len(_windows) > 100:
        for k in list(_windows.keys()):
            if not _windows[k] or now - _windows[k][-1] > 60:
                _windows.pop(k, None)


def _rate_limit(user: str, limit: int, redis_url: str = "") -> None:
    if redis_url and _rate_limit_redis(user, limit, redis_url):
        return
    _rate_limit_memory(user, limit)


def current_principal(
    request: Request,
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Principal:
    p = authenticate(x_api_key, settings)
    _rate_limit(p.user, settings.rate_limit_per_minute, settings.redis_url)
    request.state.principal = p
    return p


def require(*roles: Role):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.has(*roles):
            raise Forbidden(f"Requires one of roles: {', '.join(r.value for r in roles)}")
        return p

    return dep


def require_all(*roles: Role):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if Role.ADMIN not in p.roles and not all(role in p.roles for role in roles):
            raise Forbidden(f"Requires all roles: {', '.join(role.value for role in roles)}")
        return p

    return dep


_SENSITIVE_KEY = re.compile(r"(pass(word)?|secret|token|api[-_]?key|authorization|credential|kubeconfig)", re.I)
_TEXT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:password|passwd|token|secret|api[_-]?key)\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(://[^/\s:@]+:)[^@\s]+@"), r"\1[REDACTED]@"),
]


def key_is_sensitive(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(key))


def redact_text(text: str) -> str:
    for pat, repl in _TEXT_PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if key_is_sensitive(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
