from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database.models.administrateur import Administrateur
from app.database.models.entreprise import Entreprise
from app.database.models.utilisateur import RoleEnum, StatutCompteEnum, Utilisateur

ROLE_MAP = {
    "entreprise": RoleEnum.entreprise,
    "administrateur": RoleEnum.administrateur,
}


def list_utilisateurs(db: Session, role: str = "tous", q: str | None = None):
    """Retourne une liste de dicts prêts pour l'affichage : chaque utilisateur
    est joint (LEFT JOIN) à Entreprise et Administrateur pour récupérer son nom
    et, pour les entreprises, sa date d'inscription."""
    query = (
        db.query(Utilisateur, Entreprise, Administrateur)
        .outerjoin(Entreprise, Entreprise.idEntreprise == Utilisateur.idUtilisateur)
        .outerjoin(Administrateur, Administrateur.idAdmin == Utilisateur.idUtilisateur)
    )

    role_enum = ROLE_MAP.get(role)
    if role_enum is not None:
        query = query.filter(Utilisateur.role == role_enum)

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Utilisateur.email.ilike(like),
                Entreprise.nom.ilike(like),
                Administrateur.nom.ilike(like),
            )
        )

    rows = query.order_by(Utilisateur.idUtilisateur.asc()).all()

    return [
        {
            "id": utilisateur.idUtilisateur,
            "email": utilisateur.email,
            "role": utilisateur.role.value,
            "nom": (entreprise.nom if entreprise else None)
            or (administrateur.nom if administrateur else None)
            or "—",
            "statut": utilisateur.statut_compte.value if utilisateur.statut_compte else "inactif",
            # Seules les entreprises portent une date d'inscription en base.
            "date_creation": entreprise.date_inscription if entreprise else None,
        }
        for utilisateur, entreprise, administrateur in rows
    ]


def count_by_role(db: Session) -> dict:
    return {
        "total": db.query(Utilisateur).count(),
        "entreprises": db.query(Utilisateur)
        .filter(Utilisateur.role == RoleEnum.entreprise)
        .count(),
        "administrateurs": db.query(Utilisateur)
        .filter(Utilisateur.role == RoleEnum.administrateur)
        .count(),
    }


def toggle_statut(db: Session, id_utilisateur: int, current_admin_id: int) -> None:
    if id_utilisateur == current_admin_id:
        raise HTTPException(
            status_code=400, detail="Vous ne pouvez pas désactiver votre propre compte."
        )

    user = db.query(Utilisateur).filter(Utilisateur.idUtilisateur == id_utilisateur).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    user.statut_compte = (
        StatutCompteEnum.inactif
        if user.statut_compte == StatutCompteEnum.actif
        else StatutCompteEnum.actif
    )
    db.commit()
