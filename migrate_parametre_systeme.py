"""
Migration one-shot : crée la table ParametreSysteme si elle n'existe pas,
puis insère les valeurs par défaut.

Usage :
    python migrate_parametre_systeme.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.connection import engine, SessionLocal
from app.database.models.parametre_systeme import ParametreSysteme, ensure_defaults
from sqlalchemy import inspect, text

inspector = inspect(engine)

if "ParametreSysteme" not in inspector.get_table_names():
    ParametreSysteme.__table__.create(engine)
    print("OK  Table ParametreSysteme creee.")
else:
    print("--  Table ParametreSysteme existe deja.")

db = SessionLocal()
try:
    ensure_defaults(db)
    print("OK  Valeurs par defaut inserees.")
finally:
    db.close()

print("Migration terminee.")
