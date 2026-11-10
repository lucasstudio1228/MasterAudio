"""SQLite engine + session factory cho SQLModel."""
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

_ROOT = Path(__file__).parent.parent
DATA_DIR = _ROOT / "data"
DB_PATH = DATA_DIR / "app.db"

# check_same_thread=False: cho phép ThreadPoolExecutor worker dùng chung engine.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)


def _install_project_id_guard() -> None:
    # Project ID không bao giờ dùng lại sau khi xoá (tránh nhận nhầm file sót).
    from backend.project_cleanup import install_id_guard
    install_id_guard(engine)


_install_project_id_guard()


def create_db_and_tables() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # import models để SQLModel.metadata biết các bảng trước khi create_all
    from backend import models  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: yield 1 session per request."""
    with Session(engine) as session:
        yield session
