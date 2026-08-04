"""
Migration : ajoute la colonne date_derniere_sync sur ConnexionBDD si absente.
Usage : python migrate_connexion_bdd_sync.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ConnexionBDD")]

if "date_derniere_sync" not in columns:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE ConnexionBDD ADD COLUMN date_derniere_sync DATETIME NULL"))
    print("OK  Colonne date_derniere_sync ajoutee.")
else:
    print("--  Colonne date_derniere_sync existe deja.")
print("Migration terminee.")
