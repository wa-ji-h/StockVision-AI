"""
Migration : crée la table ConnexionBDD si elle n'existe pas.
Usage : python migrate_connexion_bdd.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine
from app.database.models.connexion_bdd import ConnexionBDD
from sqlalchemy import inspect

inspector = inspect(engine)
if "ConnexionBDD" not in inspector.get_table_names():
    ConnexionBDD.__table__.create(engine)
    print("OK  Table ConnexionBDD creee.")
else:
    print("--  Table ConnexionBDD existe deja.")
print("Migration terminee.")
