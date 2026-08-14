"""
Migration : ajoute intention_erreur sur ConfigurationAnalyse.

Motif : le message d'echec de la traduction partageait message_execution avec les taches
4.1/4.2. Un lancement reussi ecrasait donc la trace, et l'echec devenait invisible.
Colonne dediee = la trace survit.

Usage : python migrate_configuration_analyse_intention_erreur.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from sqlalchemy import inspect, text

inspector = inspect(engine)
columns = [c["name"] for c in inspector.get_columns("ConfigurationAnalyse")]

with engine.begin() as conn:
    if "intention_erreur" not in columns:
        conn.execute(text("ALTER TABLE ConfigurationAnalyse ADD COLUMN intention_erreur TEXT NULL"))
        print("OK  Colonne intention_erreur ajoutee.")
    else:
        print("--  Colonne intention_erreur existe deja.")

print("Migration terminee.")
