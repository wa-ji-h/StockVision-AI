from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base

class ConfigurationAnalyse(Base):
    __tablename__ = "ConfigurationAnalyse"

    id_configuration = Column(Integer, primary_key=True, autoincrement=True)
    objectif = Column(String(255), nullable=False)
    date_creation = Column(DateTime, server_default=func.now())
    id_import = Column(Integer, ForeignKey("ImportDonnee.id_import", ondelete="CASCADE"), nullable=False)