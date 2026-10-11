"""Database connection and session management for SQLite."""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# SQLite database URL
# Creates a file: technical_interviewer.db in the backend directory
DATABASE_URL = "sqlite:///technical_interviewer.db"

# Create SQLAlchemy engine
# check_same_thread=False allows SQLite to work with FastAPI's async handling
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

# Session factory
# Used to create new database sessions for each request
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# Base class for all database models
# Models will inherit from this (e.g., class User(Base):)
Base = declarative_base()


def get_db():
    """
    Database session dependency for FastAPI routes.

    Usage in a route:
        @app.get("/items")
        def get_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Initialize the database.

    Creates all tables defined in models based on the Base metadata.
    Called on application startup.
    Handles schema migrations for backward compatibility.
    """
    Base.metadata.create_all(bind=engine)

    # Handle schema changes for existing databases
    _migrate_candidates_schema()
    _migrate_interviews_schema()
    _migrate_questions_schema()
    _migrate_evaluations_schema()
    _migrate_integrity_indexes()
    _migrate_recommendations_schema()
    _migrate_human_reviews_schema()


def _migrate_candidates_schema():
    """Add the saved-CV columns to an existing candidates table (all nullable; nothing is rewritten or removed)."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "candidates" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("candidates")}
    with engine.begin() as conn:
        for column, ddl in (("cv_text", "TEXT"), ("cv_file_name", "VARCHAR(255)"), ("cv_updated_at", "DATETIME")):
            if column not in existing:
                conn.execute(text(f"ALTER TABLE candidates ADD COLUMN {column} {ddl}"))


def _migrate_interviews_schema():
    """
    Safely add new columns to existing interviews table if they don't exist.
    Ensures backward compatibility with existing databases.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    if "interviews" not in inspector.get_table_names():
        return  # Table doesn't exist yet, create_all() will handle it

    existing_columns = {col['name'] for col in inspector.get_columns("interviews")}

    with engine.begin() as conn:
        # Add mode column if it doesn't exist
        if "mode" not in existing_columns:
            conn.execute(text("ALTER TABLE interviews ADD COLUMN mode VARCHAR(50) DEFAULT 'question_bank' NOT NULL"))

        # Add cv_text column if it doesn't exist
        if "cv_text" not in existing_columns:
            conn.execute(text("ALTER TABLE interviews ADD COLUMN cv_text TEXT"))

        # Consent columns (all nullable: existing interviews simply have no recorded decision)
        for column, ddl in (
            ("consent_status", "VARCHAR(20)"),
            ("consent_recorded_at", "DATETIME"),
            ("consent_notice_version", "VARCHAR(30)"),
            ("abandoned_at", "DATETIME"),
        ):
            if column not in existing_columns:
                conn.execute(text(f"ALTER TABLE interviews ADD COLUMN {column} {ddl}"))


def _migrate_evaluations_schema():
    """
    Safely add new columns to existing evaluations table if they don't exist.
    Ensures backward compatibility with existing databases.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    if "evaluations" not in inspector.get_table_names():
        return  # Table doesn't exist yet, create_all() will handle it

    existing_columns = {col['name'] for col in inspector.get_columns("evaluations")}

    with engine.begin() as conn:
        # Add dimensions column (JSON) if it doesn't exist
        if "dimensions" not in existing_columns:
            conn.execute(text("ALTER TABLE evaluations ADD COLUMN dimensions TEXT"))

        # Adaptive follow-up decision columns (all nullable: older evaluations simply have no decision)
        for column, ddl in (
            ("needs_follow_up", "BOOLEAN"),
            ("follow_up_reason", "VARCHAR(30)"),
            ("missing_information", "TEXT"),
            ("contradicts_cv", "BOOLEAN"),
        ):
            if column not in existing_columns:
                conn.execute(text(f"ALTER TABLE evaluations ADD COLUMN {column} {ddl}"))


def _migrate_integrity_indexes():
    """
    Add the unique indexes that stop duplicate answers, evaluations and interview-question positions.

    Same names as the model-level indexes, so this is a no-op on a freshly created database. On an
    existing database an index is skipped (with a warning) if the data already contains duplicates:
    nothing is deleted or altered, and the application-level duplicate handling still applies.
    """
    import logging
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import DatabaseError

    logger = logging.getLogger(__name__)
    tables = set(inspect(engine).get_table_names())

    for name, table, columns in (
        ("uq_answers_interview_question", "answers", "interview_question_id"),
        ("uq_evaluations_answer", "evaluations", "answer_id"),
        ("uq_interview_questions_order", "interview_questions", "interview_id, question_order"),
    ):
        if table not in tables:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({columns})"))
        except DatabaseError as e:
            logger.warning(f"Could not add unique index {name} on {table} (existing duplicate rows?): {e}")


def _migrate_questions_schema():
    """
    Safely add questions.source_interview_id to an existing questions table.
    NULL for shared question-bank rows, so existing data is unaffected.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    if "questions" not in inspector.get_table_names():
        return  # Table doesn't exist yet, create_all() will handle it

    existing_columns = {col['name'] for col in inspector.get_columns("questions")}

    with engine.begin() as conn:
        if "source_interview_id" not in existing_columns:
            conn.execute(text("ALTER TABLE questions ADD COLUMN source_interview_id INTEGER"))


def _migrate_recommendations_schema():
    """
    Safely add new columns to existing recommendations table if they don't exist.
    Ensures backward compatibility with existing databases.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    if "recommendations" not in inspector.get_table_names():
        return  # Table doesn't exist yet, create_all() will handle it

    existing_columns = {col['name'] for col in inspector.get_columns("recommendations")}

    with engine.begin() as conn:
        # Add confidence column if it doesn't exist
        if "confidence" not in existing_columns:
            conn.execute(text("ALTER TABLE recommendations ADD COLUMN confidence VARCHAR(20)"))


def _migrate_human_reviews_schema():
    """
    Safely add human_reviews table if it doesn't exist.
    New table for human review decisions (separate from AI recommendations).
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)

    # Table will be created by create_all() if it doesn't exist, no migration needed
    # This is a new table, not an alteration to existing schema
    if "human_reviews" not in inspector.get_table_names():
        # Let create_all() handle it
        pass
