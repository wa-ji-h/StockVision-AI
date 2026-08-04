from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from app.database.connection import Base


class ConnexionBDD(Base):
    """Identifiants chiffrés d'une connexion base de données tierce."""

    __tablename__ = "ConnexionBDD"

    id_connexion        = Column(Integer, primary_key=True, autoincrement=True)
    id_source           = Column(Integer, ForeignKey("SourceDonnee.id_source", ondelete="CASCADE"), nullable=False, unique=True)
    type_sgbd            = Column(String(20), nullable=False)   # mysql | postgresql | mssql
    host                 = Column(String(255), nullable=False)
    port                 = Column(Integer, nullable=False)
    db_name              = Column(String(255), nullable=False)
    user_chiffre         = Column(Text, nullable=False)         # Fernet-encrypted
    password_chiffre     = Column(Text, nullable=False)         # Fernet-encrypted
    tables_detectees     = Column(Text, nullable=True)          # JSON list of table names
    date_derniere_sync   = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<ConnexionBDD {self.type_sgbd}://{self.host}:{self.port}/{self.db_name}>"
