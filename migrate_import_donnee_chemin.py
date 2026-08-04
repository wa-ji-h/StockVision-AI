"""
Migration : ajoute la colonne chemin_fichier sur ImportDonnee si absente.
Usage : python migrate_import_donnee_chemin.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ImportDonnee")]

with engine.begin() as conn:
    if "chemin_fichier" not in columns:
        conn.execute(text("ALTER TABLE ImportDonnee ADD COLUMN chemin_fichier VARCHAR(500) NULL"))
        print("OK  Colonne chemin_fichier ajoutee.")
    else:
        print("--  Colonne chemin_fichier existe deja.")

print("Migration terminee.")
