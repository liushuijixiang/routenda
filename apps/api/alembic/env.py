from __future__ import annotations


from alembic import context
from sqlalchemy import engine_from_config, pool

from visit_agent.infrastructure.db.sqlalchemy_models import Base


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = context.get_x_argument(as_dictionary=True).get("url", "postgresql://visit_agent:visit_agent@localhost:5432/visit_agent")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config = context.config
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
