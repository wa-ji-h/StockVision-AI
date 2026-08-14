import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "stock_vision")
    DB_PORT = os.getenv("DB_PORT", "3306")

    SQLALCHEMY_DATABASE_URL = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )

    SECRET_KEY = os.getenv("SECRET_KEY")
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 60
    ADMIN_REGISTRATION_CODE = os.getenv("ADMIN_REGISTRATION_CODE", "")

    # Chiffrement des identifiants de connexion BD tierces
    DB_ENCRYPTION_KEY = os.getenv("DB_ENCRYPTION_KEY", "")

    # OAuth (Google / GitHub)
    OAUTH_REDIRECT_BASE_URL = os.getenv("OAUTH_REDIRECT_BASE_URL", "http://127.0.0.1:8000")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")

    # LLM (Module 4 — traduction de l'intention et interprétation des résultats).
    # Laissé vide = pas d'appel réseau : la traduction bascule sur le repli déterministe
    # de l'objectif prédéfini. Le système reste entièrement fonctionnel sans clé.
    #
    # LLM_PROVIDER : « gemini » (défaut, palier gratuit) ou « anthropic ». Les détails de
    # chaque API vivent dans app/services/moteur_analyse/llm_client.py, nulle part ailleurs.
    # LLM_BASE_URL est facultatif : vide, chaque fournisseur applique la sienne.
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    # Le quota gratuit est compté PAR MODÈLE, d'où l'importance de ce défaut :
    # gemini-2.5-flash est plafonné à 20 requêtes/JOUR, inutilisable en développement.
    # gemini-3.1-flash-lite est retenu pour sa latence — 2,5 s mesurées sur une
    # interprétation complète, contre 20 s pour gemini-3.5-flash à qualité comparable.
    LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.1-flash-lite")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")

    # SMTP — laissé vide en dev : les emails sont alors juste loggés en console
    # (voir app/utils/email.py) au lieu d'échouer.
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "no-reply@stockvision.ai")
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

settings = Settings()