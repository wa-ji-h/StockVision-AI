import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.database.models.entreprise import Entreprise, StatutDemandeEnum
from app.database.models.reset_token import PasswordResetToken
from app.database.models.utilisateur import StatutCompteEnum, Utilisateur
from app.utils.email import send_email

RESET_TOKEN_VALIDITY_HOURS = 48


def list_pending_entreprises(db: Session):
    """Retourne les demandes en attente sous forme de tuples (Entreprise, Utilisateur)."""
    return (
        db.query(Entreprise, Utilisateur)
        .join(Utilisateur, Entreprise.idEntreprise == Utilisateur.idUtilisateur)
        .filter(Entreprise.statut_demande == StatutDemandeEnum.en_attente)
        .order_by(Entreprise.date_inscription.asc())
        .all()
    )


def approve_entreprise(db: Session, id_entreprise: int, admin_id: int) -> None:
    entreprise = db.query(Entreprise).filter(Entreprise.idEntreprise == id_entreprise).first()
    if not entreprise:
        raise HTTPException(status_code=404, detail="Demande introuvable.")

    utilisateur = (
        db.query(Utilisateur).filter(Utilisateur.idUtilisateur == id_entreprise).first()
    )
    if not utilisateur:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    # Mot de passe temporaire aléatoire et haché — jamais communiqué tel
    # quel : seul le lien de réinitialisation ci-dessous permet à
    # l'entreprise de définir son mot de passe définitif.
    temp_password = secrets.token_urlsafe(12)
    utilisateur.mot_de_passe = hash_password(temp_password)
    utilisateur.statut_compte = StatutCompteEnum.actif

    entreprise.statut_demande = StatutDemandeEnum.validee
    entreprise.id_admin_validateur = admin_id

    reset_token = PasswordResetToken(
        token=secrets.token_urlsafe(32),
        id_utilisateur=utilisateur.idUtilisateur,
        expires_at=datetime.utcnow() + timedelta(hours=RESET_TOKEN_VALIDITY_HOURS),
        used=False,
    )
    db.add(reset_token)
    db.commit()

    reset_link = (
        f"{settings.OAUTH_REDIRECT_BASE_URL}/reinitialiser-mot-de-passe?token={reset_token.token}"
    )
    send_email(
        to=utilisateur.email,
        subject="Votre compte StockVision AI a été approuvé",
        body=(
            f"Bonjour {entreprise.nom},\n\n"
            "Bonne nouvelle : votre demande d'inscription a été approuvée par un administrateur.\n\n"
            f"Définissez votre mot de passe pour activer votre compte :\n{reset_link}\n\n"
            f"Ce lien est valable {RESET_TOKEN_VALIDITY_HOURS} heures et à usage unique.\n\n"
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
