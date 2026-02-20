import asyncio
from logging.config import fileConfig

from sqlmodel import SQLModel, create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sqlalchemy.sql.naming import ConventionDict

from alembic import context

from app.core.config import get_settings

# Add models here for 'autogenerate' support
from app.db.models.user import User


SETTINGS = get_settings()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = SQLModel.metadata

POSTGIS_MANAGED_TABLES = {
    "spatial_ref_sys",
    "_typmod_cache",
}

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

naming_convention: ConventionDict = {
    "ix": "ix_%(table_name)s_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection, 
        target_metadata=target_metadata,
        include_object=include_object,
        naming_convention=naming_convention,
        compare_type=True,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = create_async_engine(
        SETTINGS.ASYNC_DATABASE_URL,
        echo=True,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)


def include_object(object_, name, type_, reflected, compare_to):
    """Ignore tables managed by PostGIS extension during autogenerate."""
    if type_ == "table" and reflected and compare_to is None and name in POSTGIS_MANAGED_TABLES:
        return False
    return True


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
