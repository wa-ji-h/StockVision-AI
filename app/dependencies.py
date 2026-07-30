from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database.connection import get_db
from app.database.models.utilisateur import RoleEnum, Utilisateur


class NotAuthenticated(Exception):
    """Levée quand le cookie de session est absent/invalide/expiré, ou que
    le rôle de l'utilisateur ne correspond pas à la route demandée.
    Interceptée par un exception_handler dans main.py qui redirige vers
    /connexion — voir app/main.py."""


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Utilisateur:
    token = request.cookies.get("access_token")
    payload = decode_access_token(token) if token else None
    if not payload:
        raise NotAuthenticated()

    user = db.query(Utilisateur).filter(Utilisateur.idUtilisateur == int(payload["sub"])).first()
    if not user:
        raise NotAuthenticated()
    return user


def require_role(role: RoleEnum):
    def _dependency(user: Utilisateur = Depends(get_current_user)) -> Utilisateur:
        if user.role != role:
            raise NotAuthenticated()
        return user

    return _dependency
