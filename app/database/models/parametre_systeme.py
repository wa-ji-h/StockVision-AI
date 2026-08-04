from sqlalchemy import Column, String, Text

from app.database.connection import Base


class ParametreSysteme(Base):
    """Stockage clé/valeur pour les réglages globaux de la plateforme."""

    __tablename__ = "ParametreSysteme"

    cle = Column(String(100), primary_key=True, nullable=False)
    valeur = Column(Text, nullable=True)
    description = Column(String(255), nullable=True)

    def __repr__(self):
        return f"<ParametreSysteme {self.cle}={self.valeur!r}>"


# Valeurs par défaut injectées au premier accès
DEFAULTS: dict[str, tuple[str, str]] = {
    # (valeur_defaut, description)
    "frequence_analyse_defaut":  ("quotidien",  "Fréquence d'exécution par défaut des configurations d'analyse"),
    "seuil_criticite_defaut":    ("50",         "Seuil de score de criticité pour déclencher une alerte (0-100)"),
    "duree_reset_mdp_heures":    ("24",         "Durée de validité des liens de réinitialisation (heures)"),
    "duree_session_minutes":     ("60",         "Durée d'expiration de session JWT (minutes)"),
    "notif_nouvelle_demande":    ("1",          "Email à l'admin lors d'une nouvelle demande d'inscription"),
    "notif_securite":            ("0",          "Email en cas de tentatives de connexion échouées répétées"),
    "notif_resume_hebdo":        ("0",          "Récapitulatif hebdomadaire des alertes et analyses"),
}


def ensure_defaults(db) -> None:
    """Insère les valeurs par défaut manquantes — idempotent."""
    for cle, (valeur, description) in DEFAULTS.items():
        existing = db.query(ParametreSysteme).filter_by(cle=cle).first()
        if existing is None:
            db.add(ParametreSysteme(cle=cle, valeur=valeur, description=description))
    db.commit()


def get_all(db) -> dict[str, str]:
    """Retourne tous les paramètres sous forme de dict {cle: valeur}."""
    rows = db.query(ParametreSysteme).all()
    return {r.cle: r.valeur for r in rows}


def set_param(db, cle: str, valeur: str) -> None:
    row = db.query(ParametreSysteme).filter_by(cle=cle).first()
    if row:
        row.valeur = valeur
    else:
        db.add(ParametreSysteme(cle=cle, valeur=valeur))
