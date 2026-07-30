import os

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.database.models.administrateur import Administrateur
from app.database.models.entreprise import Entreprise, StatutDemandeEnum
from app.database.models.utilisateur import RoleEnum, StatutCompteEnum, Utilisateur
from app.schemas.auth import (
    LoginRequest,
    RegisterAdminRequest,
    RegisterEntrepriseRequest,
)


def _email_exists(db: Session, email: str) -> bool:
    return db.query(Utilisateur).filter(Utilisateur.email == email).first() is not None


def register_entreprise(db: Session, data: RegisterEntrepriseRequest) -> dict:
    if _email_exists(db, data.email):
        raise HTTPException(status_code=400, detail="Cet email est déjà utilisé.")

    # Pas de mot de passe à ce stade : le compte reste "inactif" tant qu'un
    # administrateur n'a pas approuvé la demande (voir entreprise_service).
    utilisateur = Utilisateur(
        email=data.email,
        mot_de_passe=None,
        role=RoleEnum.entreprise,
        statut_compte=StatutCompteEnum.inactif,
    )
    db.add(utilisateur)
    db.flush()

    entreprise = Entreprise(
        idEntreprise=utilisateur.idUtilisateur,
        nom=data.nom,
        secteur_activite=data.secteur_activite,
        statut_demande=StatutDemandeEnum.en_attente,
    )
    db.add(entreprise)
    db.commit()

    return {
        "message": "Demande d'inscription soumise avec succès.",
        "detail": "Un administrateur va examiner votre demande. Vous recevrez un email pour définir votre mot de passe une fois approuvée.",
    }


def register_administrateur(db: Session, data: RegisterAdminRequest) -> dict:
    if data.password != data.password_confirm:
        raise HTTPException(status_code=400, detail="Les mots de passe ne correspondent pas.")

    admin_secret = os.getenv("ADMIN_REGISTRATION_CODE", "")
    if not admin_secret or data.admin_code != admin_secret:
        raise HTTPException(status_code=403, detail="Code administrateur invalide.")

    if _email_exists(db, data.email):
        raise HTTPException(status_code=400, detail="Cet email est déjà utilisé.")

    utilisateur = Utilisateur(
        email=data.email,
        mot_de_passe=hash_password(data.password),
        role=RoleEnum.administrateur,
        statut_compte=StatutCompteEnum.actif,
    )
    db.add(utilisateur)
    db.flush()

    admin = Administrateur(
        idAdmin=utilisateur.idUtilisateur,
        nom=data.nom,
    )
    db.add(admin)
    db.commit()

    return {
        "message": "Compte administrateur créé avec succès.",
        "detail": "Vous pouvez maintenant vous connecter.",
    }


def login_user(db: Session, data: LoginRequest) -> dict:
    user = db.query(Utilisateur).filter(Utilisateur.email == data.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect.",
        )

    if not user.mot_de_passe:
        # Compte entreprise créé mais pas encore approuvé par un admin
        # (aucun mot de passe défini tant que l'email de reset n'est pas
        # utilisé), ou compte OAuth-only qui ne se connecte jamais par mdp.
        raise HTTPException(
            status_code=403,
            detail="Votre inscription est en attente de validation par un administrateur.",
        )

    if not verify_password(data.password, user.mot_de_passe):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect.",
        )

    if user.statut_compte == StatutCompteEnum.inactif:
        raise HTTPException(status_code=403, detail="Votre compte est désactivé.")

    if user.statut_compte == StatutCompteEnum.en_attente:
        raise HTTPException(
            status_code=403,
            detail="Votre compte est en attente de validation par un administrateur.",
        )

    token = create_access_token(
        {"sub": str(user.idUtilisateur), "email": user.email, "role": user.role.value}
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role.value,
        "email": user.email,
    }
