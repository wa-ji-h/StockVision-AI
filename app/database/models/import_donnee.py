from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base

class ImportDonnee(Base):
    __tablename__ = "ImportDonnee"

    id_import = Column(Integer, primary_key=True, autoincrement=True)
    nom_fichier = Column(String(255))
    type_format = Column(String(50))
    date_import = Column(DateTime, server_default=func.now())
    statut = Column(String(50))
    id_source = Column(Integer, ForeignKey("SourceDonnee.id_source", ondelete="CASCADE"), nullable=False)