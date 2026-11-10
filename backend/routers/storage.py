"""/api/storage — dung lượng project + quét/dọn dữ liệu thừa."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from backend.database import get_session

router = APIRouter(prefix="/api/storage", tags=["storage"])


class CleanRequest(BaseModel):
    keys: list[str]


@router.get("/usage")
def storage_usage(session: Session = Depends(get_session)):
    """Dung lượng thật trên đĩa của từng project (hardlink tính 1 lần)."""
    from backend.storage_maintenance import project_usage
    return project_usage(session)


@router.get("/junk")
def storage_junk(session: Session = Depends(get_session)):
    """Chỉ QUÉT (không xoá gì): các nhóm dữ liệu thừa + dung lượng."""
    from backend.storage_maintenance import scan
    return scan(session)


@router.post("/clean")
def storage_clean(body: CleanRequest, session: Session = Depends(get_session)):
    """Dọn các mục đã chọn — luôn quét lại, chỉ làm với khoá có trong lần quét mới."""
    from backend.storage_maintenance import clean
    return clean(session, body.keys)
