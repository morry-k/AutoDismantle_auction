# backend/db.py
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"   # ← 絶対パスに固定

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- 起動時の軽量オートマイグレーション（SQLite用） ---
def ensure_column(engine, table: str, column: str, type_sql: str) -> None:
    """存在しないカラムだけを追加する（SQLite）"""
    with engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
        if column not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {type_sql}"))

def ensure_schema(engine) -> None:
    # 必要に応じて増やせます
    ensure_column(engine, "vehicles", "lane", "TEXT")