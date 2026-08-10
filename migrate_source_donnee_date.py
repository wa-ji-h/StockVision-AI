"""
Migration : ajoute la colonne date_creation sur SourceDonnee si absente.
Usage : python migrate_source_donnee_date.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("SourceDonnee")]

with engine.begin() as conn:
    if "date_creation" not in columns:
        conn.execute(text(
            "ALTER TABLE SourceDonnee ADD COLUMN date_creation DATETIME NULL DEFAULT CURRENT_TIMESTAMP"
        ))
        print("OK  Colonne date_creation ajoutee.")
    else:
        print("--  Colonne date_creation existe deja.")

print("Migration terminee.")
