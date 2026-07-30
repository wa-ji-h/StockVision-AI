from app.database.connection import Base, engine
from app.database import models  # noqa: F401 — nécessaire pour enregistrer tous les modèles

def init_db():
    Base.metadata.create_all(bind=engine)
    print("✅ Connexion réussie, tables synchronisées avec les modèles.")

if __name__ == "__main__":
    init_db()