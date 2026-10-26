from alembic import context
from quality.config import Settings
from quality.db import Database
from quality.models import Base

settings = Settings()
target_metadata = Base.metadata
connection = context.config.attributes.get("connection")
if connection is not None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    db = Database(settings, migration=True)
    with db.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
