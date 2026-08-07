"""FastAPI transport layer — thin HTTP adapter over ApplicationService.

Architecture:
    Browser → FastAPI routes → ApplicationService → Application/Control Plane → Engines

Rules:
    - Routes contain NO business logic.
    - All state mutations go through ApplicationService.
    - All reads go through ApplicationService queries or CP service layer.
    - This module must never import sqlite3, database.py, repositories, or
      engine-private modules directly.
"""
