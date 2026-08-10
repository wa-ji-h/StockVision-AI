"""
Migration : ajoute les colonnes facteurs_selectionnes, frequence, statut sur ConfigurationAnalyse.
Usage : python migrate_configuration_analyse.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ConfigurationAnalyse")]

with engine.begin() as conn:
    if "facteurs_selectionnes" not in columns:
        conn.execute(text("ALTER TABLE ConfigurationAnalyse ADD COLUMN facteurs_selectionnes TEXT NULL"))
        print("OK  Colonne facteurs_selectionnes ajoutee.")
    else:
        print("--  Colonne facteurs_selectionnes existe deja.")

    if "frequence" not in columns:
        conn.execute(text("ALTER TABLE ConfigurationAnalyse ADD COLUMN frequence VARCHAR(20) NULL"))
        print("OK  Colonne frequence ajoutee.")
    else:
        print("--  Colonne frequence existe deja.")

    if "statut" not in columns:
        conn.execute(text("ALTER TABLE ConfigurationAnalyse ADD COLUMN statut VARCHAR(20) NOT NULL DEFAULT 'brouillon'"))
        print("OK  Colonne statut ajoutee.")
    else:
        print("--  Colonne statut existe deja.")

print("Migration terminee.")
