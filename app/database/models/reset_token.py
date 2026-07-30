from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from app.database.connection import Base


class PasswordResetToken(Base):
    __tablename__ = "PasswordResetToken"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(255), unique=True, nullable=False, index=True)
    id_utilisateur = Column(
        Integer, ForeignKey("Utilisateur.idUtilisateur", ondelete="CASCADE"), nullable=False
    )
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
