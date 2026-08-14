"""
Migration : ajoute les colonnes taille_octets et meta_json sur ImportDonnee si absentes.
Usage : python migrate_import_donnee_meta.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ImportDonnee")]

with engine.begin() as conn:
    if "taille_octets" not in columns:
        conn.execute(text("ALTER TABLE ImportDonnee ADD COLUMN taille_octets INT NULL"))
        print("OK  Colonne taille_octets ajoutee.")
    else:
        print("--  Colonne taille_octets existe deja.")

    if "meta_json" not in columns:
        conn.execute(text("ALTER TABLE ImportDonnee ADD COLUMN meta_json TEXT NULL"))
        print("OK  Colonne meta_json ajoutee.")
    else:
        print("--  Colonne meta_json existe deja.")

print("Migration terminee.")
