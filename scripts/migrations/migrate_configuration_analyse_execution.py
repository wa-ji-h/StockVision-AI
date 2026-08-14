"""
Migration : ajoute les colonnes préparant le Module 4 (moteur d'analyse IA) sur ConfigurationAnalyse.
Usage : python migrate_configuration_analyse_execution.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ConfigurationAnalyse")]

ADD = [
    ("statut_execution", "ALTER TABLE ConfigurationAnalyse ADD COLUMN statut_execution VARCHAR(30) NULL"),
    ("derniere_execution", "ALTER TABLE ConfigurationAnalyse ADD COLUMN derniere_execution DATETIME NULL"),
    ("prochaine_execution", "ALTER TABLE ConfigurationAnalyse ADD COLUMN prochaine_execution DATETIME NULL"),
    ("payload_execution", "ALTER TABLE ConfigurationAnalyse ADD COLUMN payload_execution TEXT NULL"),
]

with engine.begin() as conn:
    for col_name, ddl in ADD:
        if col_name not in columns:
            conn.execute(text(ddl))
            print(f"OK  Colonne {col_name} ajoutee.")
        else:
            print(f"--  Colonne {col_name} existe deja.")

print("Migration terminee.")
