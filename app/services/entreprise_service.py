import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.database.models.entreprise import Entreprise, StatutDemandeEnum
from app.database.models.reset_token import PasswordResetToken
from app.database.models.utilisateur import StatutCompteEnum, Utilisateur
from app.utils.email import send_email

RESET_TOKEN_VALIDITY_HOURS = 48


STATUT_MAP = {
    "en_attente": StatutDemandeEnum.en_attente,
    "validee": StatutDemandeEnum.validee,
    "refusee": StatutDemandeEnum.refusee,
}


def list_entreprises(db: Session, statut: str = "en_attente", q: str | None = None):
    """Retourne les entreprises (Entreprise, Utilisateur), filtrées par statut
    de demande ("en_attente", "validee", "refusee", ou "tous") et par une
    recherche libre sur le nom, le secteur ou l'email."""
    query = db.query(Entreprise, Utilisateur).join(
        Utilisateur, Entreprise.idEntreprise == Utilisateur.idUtilisateur
    )
    enum_value = STATUT_MAP.get(statut)
    if enum_value is not None:
        query = query.filter(Entreprise.statut_demande == enum_value)

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Entreprise.nom.ilike(like),
                Entreprise.secteur_activite.ilike(like),
                Utilisateur.email.ilike(like),
            )
        )

    return query.order_by(Entreprise.date_inscription.desc()).all()


def list_pending_entreprises(db: Session):
    """Compat: demandes en attente uniquement."""
    return list_entreprises(db, statut="en_attente")


def approve_entreprise(db: Session, id_entreprise: int, admin_id: int) -> None:
    entreprise = db.query(Entreprise).filter(Entreprise.idEntreprise == id_entreprise).first()
    if not entreprise:
        raise HTTPException(status_code=404, detail="Demande introuvable.")

    utilisateur = (
        db.query(Utilisateur).filter(Utilisateur.idUtilisateur == id_entreprise).first()
    )
    if not utilisateur:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    # Génère un mot de passe temporaire sécurisé, envoyé en clair par email.
    # L'entreprise devra le changer dès la première connexion (doit_changer_mdp=True).
    temp_password = secrets.token_urlsafe(12)
    utilisateur.mot_de_passe = hash_password(temp_password)
    utilisateur.statut_compte = StatutCompteEnum.actif
    utilisateur.doit_changer_mdp = True

    entreprise.statut_demande = StatutDemandeEnum.validee
    entreprise.id_admin_validateur = admin_id

    db.commit()

    login_url = f"{settings.OAUTH_REDIRECT_BASE_URL}/connexion"
    send_email(
        to=utilisateur.email,
        subject="Votre compte StockVision AI a été approuvé",
        body=(
            f"Bonjour {entreprise.nom},\n\n"
            "Bonne nouvelle : votre demande d'inscription a été approuvée par un administrateur.\n\n"
            "Voici vos identifiants de connexion :\n"
            f"  Email              : {utilisateur.email}\n"
            f"  Mot de passe temporaire : {temp_password}\n\n"
            f"Connectez-vous sur : {login_url}\n\n"
            "Pour des raisons de sécurité, vous devrez définir un nouveau mot de passe "
            "dès votre première connexion.\n\n"
            "L'équipe StockVision AI"
        ),
    )


def reject_entreprise(db: Session, id_entreprise: int) -> None:
    entreprise = db.query(Entreprise).filter(Entreprise.idEntreprise == id_entreprise).first()
    if not entreprise:
        raise HTTPException(status_code=404, detail="Demande introuvable.")
    entreprise.statut_demande = StatutDemandeEnum.refusee
    db.commit()


def _get_valid_token(db: Session, token: str) -> PasswordResetToken:
    row = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    if not row or row.used or row.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=400, detail="Ce lien de réinitialisation est invalide ou a expiré."
        )
    return row


def is_reset_token_valid(db: Session, token: str) -> bool:
    row = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    return bool(row and not row.used and row.expires_at > datetime.utcnow())


def set_new_password(db: Session, token: str, new_password: str) -> None:
    row = _get_valid_token(db, token)
    user = db.query(Utilisateur).filter(Utilisateur.idUtilisateur == row.id_utilisateur).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    user.mot_de_passe = hash_password(new_password)
    user.statut_compte = StatutCompteEnum.actif
    row.used = True
    db.commit()
