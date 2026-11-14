from pathlib import Path

from alembic import command
from alembic.config import Config

from app.database import _database_url


def run_migrations() -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _database_url().replace("%", "%%"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    run_migrations()
