import os
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models.entreprise import Entreprise, StatutDemandeEnum
from app.database.models.utilisateur import RoleEnum, Utilisateur
from app.dependencies import require_role
from app.services import entreprise_service

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["asset_version"] = str(int(time.time()))

router = APIRouter(tags=["dashboard"])

SIDEBAR_TITLES = {
    "entreprises": "Entreprises actives",
    "imports": "Imports",
    "configurations": "Configurations d'analyse",
    "alertes": "Alertes globales",
    "utilisateurs": "Utilisateurs",
    "parametres": "Paramètres système",
}


def _pending_count(db: Session) -> int:
    return (
        db.query(Entreprise)
        .filter(Entreprise.statut_demande == StatutDemandeEnum.en_attente)
        .count()
    )


@router.get("/dashboard/admin")
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    stats = {
        "demandes_en_attente": _pending_count(db),
        "entreprises_actives": db.query(Entreprise)
        .filter(Entreprise.statut_demande == StatutDemandeEnum.validee)
        .count(),
        "utilisateurs": db.query(Utilisateur).count(),
    }
    return templates.TemplateResponse(
        request,
        "pages/dashboard/admin_overview.html",
        {"user": user, "active_page": "overview", "pending_count": stats["demandes_en_attente"], "stats": stats},
    )


@router.get("/dashboard/admin/demandes")
def admin_demandes(
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    demandes = entreprise_service.list_pending_entreprises(db)
    return templates.TemplateResponse(
        request,
        "pages/dashboard/admin_demandes.html",
        {
            "user": user,
            "active_page": "demandes",
            "pending_count": len(demandes),
            "demandes": demandes,
        },
    )


@router.post("/dashboard/admin/demandes/{id_entreprise}/approuver")
def admin_demande_approuver(
    id_entreprise: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    entreprise_service.approve_entreprise(db, id_entreprise, user.idUtilisateur)
    return RedirectResponse(url="/dashboard/admin/demandes", status_code=303)


@router.post("/dashboard/admin/demandes/{id_entreprise}/refuser")
def admin_demande_refuser(
    id_entreprise: int,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    entreprise_service.reject_entreprise(db, id_entreprise)
    return RedirectResponse(url="/dashboard/admin/demandes", status_code=303)


@router.get("/dashboard/admin/{section}")
def admin_placeholder(
    section: str,
    request: Request,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(require_role(RoleEnum.administrateur)),
):
    title = SIDEBAR_TITLES.get(section, section.replace("-", " ").title())
    return templates.TemplateResponse(
        request,
        "pages/dashboard/placeholder.html",
        {"user": user, "active_page": section, "pending_count": _pending_count(db), "title": title},
    )


@router.get("/dashboard/entreprise")
def entreprise_dashboard(
    request: Request,
    user: Utilisateur = Depends(require_role(RoleEnum.entreprise)),
):
    return templates.TemplateResponse(
        request, "pages/dashboard/entreprise_overview.html", {"user": user}
    )


@router.get("/deconnexion")
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("access_token")
    return response
