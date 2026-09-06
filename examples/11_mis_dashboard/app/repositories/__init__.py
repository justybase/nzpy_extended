"""Repository layer — data access behind a stable interface.

Services depend on `MISRepository` (see base.py), so the storage backend can
be swapped (cached Netezza tables, a fake in-memory dataset for tests, ...)
without touching business logic.
"""

from app.repositories.base import MISRepository
from app.repositories.cached import CachedMISRepository
from app.repositories.scoped import ScopedMISRepository
from app.repositories.temporal import AsOfMISRepository

__all__ = ["MISRepository", "CachedMISRepository", "ScopedMISRepository",
           "AsOfMISRepository"]
