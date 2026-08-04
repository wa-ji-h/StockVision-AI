from sqlalchemy import Boolean, Column, Enum, Integer, String

from app.database.connection import Base
import enum

class RoleEnum(str, enum.Enum):
    entreprise = "entreprise"
    administrateur = "administrateur"

class StatutCompteEnum(str, enum.Enum):
    actif = "actif"
    inactif = "inactif"
    en_attente = "en_attente"

class Utilisateur(Base):
    __tablename__ = "Utilisateur"

    idUtilisateur = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(150), unique=True, nullable=False)
    # Nullable: accounts created via Google/GitHub OAuth have no local password.
    mot_de_passe = Column(String(255), nullable=True)
    role = Column(Enum(RoleEnum), nullable=False)
    statut_compte = Column(Enum(StatutCompteEnum), default=StatutCompteEnum.en_attente)
    oauth_provider = Column(String(20), nullable=True)
    oauth_id = Column(String(255), nullable=True)
    doit_changer_mdp = Column(Boolean, default=False, nullable=False, server_default="0")