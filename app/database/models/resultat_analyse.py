from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base

class ResultatAnalyse(Base):
    __tablename__ = "ResultatAnalyse"

    id_resultat = Column(Integer, primary_key=True, autoincrement=True)
    date_execution = Column(DateTime, server_default=func.now())
    valeur_analyse = Column(Text)
    valeur_prevue = Column(Text)
    type_analyse = Column(String(100))
    id_configuration = Column(Integer, ForeignKey("ConfigurationAnalyse.id_configuration", ondelete="CASCADE"), nullable=False)