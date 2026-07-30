from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.connection import get_db
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    RegisterAdminRequest,
    RegisterEntrepriseRequest,
    ResetPasswordRequest,
)
from app.services import auth_service, entreprise_service

router = APIRouter(tags=["auth"])


def _redirect_for_role(role: str) -> str:
    return "/dashboard/admin" if role == "administrateur" else "/dashboard/entreprise"


@router.post("/inscription", response_model=MessageResponse)
def inscription(data: RegisterEntrepriseRequest, db: Session = Depends(get_db)):
    result = auth_service.register_entreprise(db, data)
    return MessageResponse(**result)


@router.post("/connexion")
def connexion(data: LoginRequest, response: Response, db: Session = Depends(get_db)):
    result = auth_service.login_user(db, data)

    response.set_cookie(
        key="access_token",
        value=result["access_token"],
        httponly=True,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    return {
        "message": "Connexion réussie.",
        "role": result["role"],
        "redirect": _redirect_for_role(result["role"]),
    }


@router.post("/api/auth/register/admin", response_model=MessageResponse)
def register_admin(data: RegisterAdminRequest, db: Session = Depends(get_db)):
    result = auth_service.register_administrateur(db, data)
    return MessageResponse(**result)


@router.post("/api/auth/reset-password", response_model=MessageResponse)
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    if data.password != data.password_confirm:
        raise HTTPException(status_code=400, detail="Les mots de passe ne correspondent pas.")
    entreprise_service.set_new_password(db, data.token, data.password)
    return MessageResponse(
        message="Mot de passe défini avec succès.",
        detail="Vous pouvez maintenant vous connecter.",
    )
