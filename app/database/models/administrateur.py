from sqlalchemy import Column, Integer, String, ForeignKey
from app.database.connection import Base

class Administrateur(Base):
    __tablename__ = "Administrateur"

    idAdmin = Column(Integer, ForeignKey("Utilisateur.idUtilisateur", ondelete="CASCADE"), primary_key=True)
    nom = Column(String(100), nullable=False)