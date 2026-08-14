"""
Migration : ajoute la colonne precision_complementaire sur ConfigurationAnalyse.
Usage : python migrate_configuration_analyse_precision.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ConfigurationAnalyse")]

with engine.begin() as conn:
    if "precision_complementaire" not in columns:
        conn.execute(text("ALTER TABLE ConfigurationAnalyse ADD COLUMN precision_complementaire TEXT NULL"))
        print("OK  Colonne precision_complementaire ajoutee.")
    else:
        print("--  Colonne precision_complementaire existe deja.")

print("Migration terminee.")
