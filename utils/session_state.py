"""
Redis-backed session state for the voice agent.

Falls back transparently to in-memory dicts when Redis is unreachable so the
server can still start and run in development without a local Redis instance.

Environment variable
--------------------
REDIS_URL   Redis connection URL  (default: redis://localhost:6379/0)
"""

import json
import os

import redis as _redis
from loguru import logger

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SESSION_TTL: int = 3600  # seconds – 1 hour per active call session

_REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")


# ---------------------------------------------------------------------------
# Client initialisation (synchronous; called once at module import time)
# ---------------------------------------------------------------------------

def _connect_redis() -> "_redis.Redis | None":
    """
    Try to create a Redis client and verify reachability with PING.
    Returns the connected client, or None if Redis is not reachable.
    """
    try:
        client: _redis.Redis = _redis.Redis.from_url(
            _REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        logger.info(f"[SessionState] Redis connected at {_REDIS_URL}")
        return client
    except Exception as exc:
        logger.warning(
            f"[SessionState] Redis not reachable ({exc}). "
            "Falling back to in-memory storage — sessions will be lost on server restart."
        )
        return None


_redis_client: "_redis.Redis | None" = _connect_redis()


# ---------------------------------------------------------------------------
# RedisDict — dict-like wrapper with JSON serialisation + TTL
# ---------------------------------------------------------------------------

class RedisDict:
    """
    A dict-like object backed by a Redis hash namespace.

    All values are JSON-serialised before storage so arbitrary Python dicts,
    lists, and primitives round-trip correctly.  Every key is written with
    ``SESSION_TTL`` so stale call data is cleaned up automatically.

    If Redis is unavailable (client is None, or an operation raises), the
    class falls back silently to an in-memory ``dict`` so callers never see
    an exception due to Redis being down.
    """

    def __init__(
        self,
        client: "_redis.Redis | None",
        prefix: str,
        ttl: int = SESSION_TTL,
    ) -> None:
        self._client = client
        self._prefix = prefix
        self._ttl = ttl
        self._mem: dict = {}  # in-memory fallback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    # ------------------------------------------------------------------
    # dict protocol
    # ------------------------------------------------------------------

    def __setitem__(self, key, value) -> None:
        if self._client is not None:
            try:
                self._client.setex(self._k(key), self._ttl, json.dumps(value))
                return
            except Exception as exc:
                logger.warning(
                    f"[SessionState] Redis SETEX failed ({exc}); falling back to in-memory"
                )
        self._mem[key] = value

    def __getitem__(self, key):
        if self._client is not None:
            try:
                raw = self._client.get(self._k(key))
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning(
                    f"[SessionState] Redis GET failed ({exc}); falling back to in-memory"
                )
                # Let _mem lookup below handle it
            else:
                # Redis answered but key was absent — check fallback before raising
                if key in self._mem:
                    return self._mem[key]
                raise KeyError(key)
        # Redis unavailable path
        if key not in self._mem:
            raise KeyError(key)
        return self._mem[key]

    def __delitem__(self, key) -> None:
        deleted_in_redis = False
        if self._client is not None:
            try:
                deleted_in_redis = bool(self._client.delete(self._k(key)))
            except Exception as exc:
                logger.warning(
                    f"[SessionState] Redis DEL failed ({exc}); falling back to in-memory"
                )
        # If Redis confirmed deletion, we are done.  Otherwise try _mem.
        if not deleted_in_redis:
            if key not in self._mem:
                raise KeyError(key)
            del self._mem[key]
        else:
            # Also remove from fallback in case it was shadowed there.
            self._mem.pop(key, None)

    def __contains__(self, key) -> bool:
        if self._client is not None:
            try:
                return self._client.exists(self._k(key)) > 0
            except Exception as exc:
                logger.warning(
                    f"[SessionState] Redis EXISTS failed ({exc}); falling back to in-memory"
                )
        return key in self._mem

    def get(self, key, default=None):
        """Return the value for *key* if present, else *default*."""
        if self._client is not None:
            try:
                raw = self._client.get(self._k(key))
                if raw is not None:
                    return json.loads(raw)
                # Key absent from Redis — check _mem (populated during Redis downtime)
                return self._mem.get(key, default)
            except Exception as exc:
                logger.warning(
                    f"[SessionState] Redis GET failed ({exc}); falling back to in-memory"
                )
        return self._mem.get(key, default)

    def pop(self, key, *args):
        """Remove and return the value for *key*; return *default* if absent."""
        if self._client is not None:
            try:
                pipe = self._client.pipeline()
                pipe.get(self._k(key))
                pipe.delete(self._k(key))
                raw, _deleted = pipe.execute()
                # Also evict from in-memory fallback
                self._mem.pop(key, None)
                if raw is not None:
                    return json.loads(raw)
                # Key was not in Redis — fall through to default logic
                if args:
                    return args[0]
                raise KeyError(key)
            except KeyError:
                raise
            except Exception as exc:
                logger.warning(
                    f"[SessionState] Redis PIPELINE failed ({exc}); falling back to in-memory"
                )
        # Pure in-memory path (Redis unavailable or just failed above)
        if args:
            return self._mem.pop(key, args[0])
        return self._mem.pop(key)


# ---------------------------------------------------------------------------
# Module-level session namespaces (drop-in replacements for the old dicts)
# ---------------------------------------------------------------------------

conversation_states = RedisDict(_redis_client, "conv:")
call_sessions       = RedisDict(_redis_client, "sess:")
language_sessions   = RedisDict(_redis_client, "lang:")
call_states         = RedisDict(_redis_client, "state:")
