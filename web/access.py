"""Optional closed-beta authentication and per-principal request budgets."""

import hashlib
import hmac
import json
import re
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from evidence import QueryError, unique_object
from jobs import JobStore


class Access:
    def __init__(self, root, origin=None, credentials=None):
        self.root = root
        self.origin = origin
        self.accounts = {}
        self.buckets = {}
        self.lock = threading.Lock()
        if origin is None and credentials is None:
            return
        try:
            url = urlsplit(origin)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.path
                or url.query
                or url.fragment
                or origin != "https://" + url.netloc
            ):
                raise ValueError
            records = json.loads(credentials, object_pairs_hook=unique_object)
            if not isinstance(records, dict) or not 1 <= len(records) <= 16:
                raise ValueError
            if len(set(records.values())) != len(records):
                raise ValueError
            for user, digest in records.items():
                if (
                    not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", user)
                    or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                ):
                    raise ValueError
            self.accounts = records
        except (QueryError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise ValueError(
                "Closed beta requires an exact HTTPS origin and unique token SHA-256 digests"
            ) from None

    def authenticate(self, header):
        if not self.accounts:
            return "local"
        token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
        if not re.fullmatch(r"[a-zA-Z0-9_-]{32,128}", token):
            raise QueryError("Authentication required", "authentication_required")
        digest = hashlib.sha256(token.encode()).hexdigest()
        user = next(
            (name for name, valid in self.accounts.items() if hmac.compare_digest(valid, digest)),
            None,
        )
        if user is None:
            raise QueryError("Authentication required", "authentication_required")
        return user

    def store(self, user):
        if not self.accounts:
            return self.root
        return JobStore(self.root.root / "accounts" / user)

    def limit(self, user, method):
        key = user, method
        now = time.monotonic()
        limit, span = (8, 60) if method == "POST" else (45, 10)
        with self.lock:
            bucket = self.buckets.setdefault(key, deque())
            while bucket and bucket[0] <= now - span:
                bucket.popleft()
            if len(bucket) >= limit:
                raise QueryError("Request budget exhausted", "api_rate_limit")
            bucket.append(now)
