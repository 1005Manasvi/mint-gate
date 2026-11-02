"""Alembic environment: URL from config.DATABASE_URL, metadata from db.models."""

from alembic import context
from sqlalchemy import create_engine

import config
from db.models import Base

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    url = context.config.attributes.get("url") or config.DATABASE_URL
    connectable = create_engine(url)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
