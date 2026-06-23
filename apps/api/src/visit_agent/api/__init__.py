"""API package bootstrap.

Install the FK-safe SQLAlchemy repository before importing ``visit_agent.api.app``.
Python imports this package module before the ``app`` submodule, so the existing
application factory keeps its public import path while receiving the corrected
repository implementation.
"""

from typing import Any, cast

from visit_agent.infrastructure.db import sqlalchemy_repository as _repository_module
from visit_agent.infrastructure.db.ordered_sqlalchemy_repository import (
    OrderedSQLAlchemyRepository,
)

cast(Any, _repository_module).SQLAlchemyRepository = OrderedSQLAlchemyRepository
