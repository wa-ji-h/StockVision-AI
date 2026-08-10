"""
Migration : ajoute fiabilite_execution et points_execution sur ConfigurationAnalyse
(Module 4, tache 4.2). Le volume ne bloque plus une analyse : il qualifie sa fiabilite,
qui doit rester consultable sur la page des resultats bien apres le lancement.
Usage : python migrate_configuration_analyse_fiabilite.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ConfigurationAnalyse")]

ADD = [
    ("fiabilite_execution", "ALTER TABLE ConfigurationAnalyse ADD COLUMN fiabilite_execution VARCHAR(20) NULL"),
    ("points_execution", "ALTER TABLE ConfigurationAnalyse ADD COLUMN points_execution INT NULL"),
]

with engine.begin() as conn:
    for col_name, ddl in ADD:
        if col_name not in columns:
            conn.execute(text(ddl))
            print(f"OK  Colonne {col_name} ajoutee.")
        else:
            print(f"--  Colonne {col_name} existe deja.")

print("Migration terminee.")
