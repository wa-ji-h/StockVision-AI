"""
Migration : ajoute message_execution sur ConfigurationAnalyse (Module 4, tache 4.1).
Stocke le message de la derniere execution (succes ou erreur d'extraction),
consultable par l'entreprise dans le modal de detail.
Usage : python migrate_configuration_analyse_message.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ConfigurationAnalyse")]

ADD = [
    ("message_execution", "ALTER TABLE ConfigurationAnalyse ADD COLUMN message_execution TEXT NULL"),
]

with engine.begin() as conn:
    for col_name, ddl in ADD:
        if col_name not in columns:
            conn.execute(text(ddl))
            print(f"OK  Colonne {col_name} ajoutee.")
        else:
            print(f"--  Colonne {col_name} existe deja.")

print("Migration terminee.")
