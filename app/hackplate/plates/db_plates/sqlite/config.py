import logging
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.hackplate.plates.abstract_plates import DatabasePlate
from app.hackplate.toml_settings import DatabaseSettings

logger = logging.getLogger(__name__)


class SQLiteSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SQLITE_",
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
    )

    db_path: str = "db.sqlite3"


class SQLitePlate(DatabasePlate):
    def __init__(self, toml_settings: DatabaseSettings):
        self.env_settings = SQLiteSettings()
        self.toml_settings = toml_settings
        self.engine = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def connect(self) -> None:
        logger.info("Connecting to sqlite file...")
        resolved = str(Path(self.env_settings.db_path).resolve())
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{resolved}")
        self._session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        if not self.toml_settings.alembic:
            logger.info(
                "Alembic disabled, using SQLModel metadata to create database models..."
            )
            async with self.engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)

    async def disconnect(self) -> None:
        if self.engine:
            logger.info("Disconnecting from sqlite file...")
            await self.engine.dispose()
            self.engine = None
            self._session_factory = None

    async def ping(self) -> bool:
        if not self._session_factory:
            logger.warning("Ping failed, session factory not found.")
            return False
        try:
            async with self._session_factory() as session:
                await session.exec(select(1))
            return True
        except Exception:
            return False

    def get_db(self) -> AsyncSession:
        return self._session_factory()
