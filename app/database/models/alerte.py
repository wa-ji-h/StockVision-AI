from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base

class Alerte(Base):
    __tablename__ = "Alerte"

    id_alerte = Column(Integer, primary_key=True, autoincrement=True)
    type_alerte = Column(String(100))
    message = Column(Text)
    niveau = Column(String(50))
    date_creation = Column(DateTime, server_default=func.now())
    id_configuration = Column(Integer, ForeignKey("ConfigurationAnalyse.id_configuration", ondelete="CASCADE"), unique=True, nullable=False)