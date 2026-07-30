from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.database.models.entreprise import Entreprise, StatutDemandeEnum
from app.database.models.utilisateur import RoleEnum, StatutCompteEnum, Utilisateur


def login_or_create_oauth_user(
    db: Session, *, provider: str, provider_id: str, email: str, name: str | None
) -> dict:
    """Find an existing Utilisateur by email, or create a new one (as an
    "entreprise" pending approval — same rule as the manual signup form,
    since OAuth only gives us a name/email, not company details).
    """
    user = db.query(Utilisateur).filter(Utilisateur.email == email).first()

    if user is None:
        user = Utilisateur(
            email=email,
            mot_de_passe=None,
            role=RoleEnum.entreprise,
            statut_compte=StatutCompteEnum.en_attente,
            oauth_provider=provider,
            oauth_id=provider_id,
        )
        db.add(user)
        db.flush()

        entreprise = Entreprise(
            idEntreprise=user.idUtilisateur,
            nom=name or email.split("@")[0],
            secteur_activite="Non renseigné",
            statut_demande=StatutDemandeEnum.en_attente,
        )
        db.add(entreprise)
        db.commit()
        db.refresh(user)
    elif not user.oauth_provider:
        # Existing account signing in with OAuth for the first time — link it.
        user.oauth_provider = provider
        user.oauth_id = provider_id
        db.commit()

    if user.statut_compte == StatutCompteEnum.inactif:
        raise HTTPException(status_code=403, detail="Votre compte est désactivé.")

    if user.statut_compte == StatutCompteEnum.en_attente:
        raise HTTPException(
            status_code=403,
            detail="Votre demande d'inscription est en attente de validation par un administrateur.",
        )

    token = create_access_token(
        {"sub": str(user.idUtilisateur), "email": user.email, "role": user.role.value}
    )
    return {"access_token": token, "role": user.role.value}
