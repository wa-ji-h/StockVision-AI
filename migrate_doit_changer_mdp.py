"""
Script de migration one-shot : ajoute la colonne doit_changer_mdp à la
table Utilisateur si elle n'existe pas encore.

Exécuter une seule fois :
    python migrate_doit_changer_mdp.py
"""
from app.database.connection import engine
from sqlalchemy import text

ADD_COLUMN_SQL = """
ALTER TABLE Utilisateur
ADD COLUMN doit_changer_mdp TINYINT(1) NOT NULL DEFAULT 0;
"""

CHECK_COLUMN_SQL = """
SELECT COUNT(*) FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME   = 'Utilisateur'
  AND COLUMN_NAME  = 'doit_changer_mdp';
"""

with engine.connect() as conn:
    result = conn.execute(text(CHECK_COLUMN_SQL))
    exists = result.scalar()
    if exists:
        print("OK  Colonne doit_changer_mdp deja presente - rien a faire.")
    else:
        conn.execute(text(ADD_COLUMN_SQL))
        conn.commit()
        print("OK  Colonne doit_changer_mdp ajoutee avec succes.")
