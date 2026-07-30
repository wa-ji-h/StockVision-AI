from sqlalchemy import Column, Integer, String, ForeignKey
from app.database.connection import Base

class SourceDonnee(Base):
    __tablename__ = "SourceDonnee"

    id_source = Column(Integer, primary_key=True, autoincrement=True)
    type_source = Column(String(50), nullable=False)
    nom = Column(String(150))
    statut = Column(String(50))
    idEntreprise = Column(Integer, ForeignKey("Entreprise.idEntreprise", ondelete="CASCADE"), nullable=False)