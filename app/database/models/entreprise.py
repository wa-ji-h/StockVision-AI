from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base
import enum

class StatutDemandeEnum(str, enum.Enum):
    en_attente = "en_attente"
    validee = "validee"
    refusee = "refusee"

class Entreprise(Base):
    __tablename__ = "Entreprise"

    idEntreprise = Column(Integer, ForeignKey("Utilisateur.idUtilisateur", ondelete="CASCADE"), primary_key=True)
    nom = Column(String(150), nullable=False)
    secteur_activite = Column(String(100))
    statut_demande = Column(Enum(StatutDemandeEnum), default=StatutDemandeEnum.en_attente)
    date_inscription = Column(DateTime, server_default=func.now())
    id_admin_validateur = Column(Integer, ForeignKey("Administrateur.idAdmin", ondelete="SET NULL"), nullable=True)