"""PostgreSQL-only SQLAlchemy setup. No implicit SQLite or schema creation."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings


class Base(DeclarativeBase):
    pass


class AssignmentRecord(Base):
    __tablename__ = "assignments"
    from sqlalchemy import Column, DateTime, String, Text
    id = Column(String(36), primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    policy_level = Column(String(6), nullable=False)
    course = Column(String(200), nullable=False, default="")
    institution_id = Column(String(100), nullable=True, index=True)
    creator_id = Column(String(100), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


engine = None
SessionLocal = None
database_configured = bool(settings.database_url)
if database_configured:
    if not settings.database_url.lower().startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("DATABASE_URL must use PostgreSQL (postgresql:// or postgresql+psycopg://)")
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def check_database() -> bool:
    if engine is None:
        return False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_db():
    if SessionLocal is None:
        yield None
        return
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
