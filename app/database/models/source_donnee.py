from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base

class SourceDonnee(Base):
    __tablename__ = "SourceDonnee"

    id_source = Column(Integer, primary_key=True, autoincrement=True)
    type_source = Column(String(50), nullable=False)
    nom = Column(String(150))
    statut = Column(String(50))
    idEntreprise = Column(Integer, ForeignKey("Entreprise.idEntreprise", ondelete="CASCADE"), nullable=False)
    date_creation = Column(DateTime, server_default=func.now())