"""Shared slowapi rate limiter instance.

Imported by main.py (to register on the app) and by route modules
(to apply per-endpoint limits via the @limiter.limit decorator).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
