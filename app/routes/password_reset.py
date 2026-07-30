import os

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services import entreprise_service

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals.setdefault("asset_version", "1")

router = APIRouter(tags=["password-reset"])


@router.get("/reinitialiser-mot-de-passe")
def reset_password_page(request: Request, token: str, db: Session = Depends(get_db)):
    valid = entreprise_service.is_reset_token_valid(db, token)
    return templates.TemplateResponse(
        request, "pages/reset_password.html", {"token": token, "valid": valid}
    )
