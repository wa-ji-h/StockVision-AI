from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base

class ConfigurationAnalyse(Base):
    __tablename__ = "ConfigurationAnalyse"

    id_configuration = Column(Integer, primary_key=True, autoincrement=True)
    objectif = Column(String(255), nullable=False)
    date_creation = Column(DateTime, server_default=func.now())
    id_import = Column(Integer, ForeignKey("ImportDonnee.id_import", ondelete="CASCADE"), nullable=False)
    facteurs_selectionnes = Column(Text, nullable=True)  # JSON: liste des tables/colonnes choisies (étape 1)
    frequence = Column(String(20), nullable=True)         # ponctuelle | quotidienne | hebdomadaire | mensuelle
    statut = Column(String(20), nullable=False, default="brouillon")  # brouillon | actif
    precision_complementaire = Column(Text, nullable=True)  # texte libre optionnel (étape 3)

    # Module 4 (moteur d'analyse IA) — pas encore développé : ces champs préparent le terrain
    # (état/UI uniquement, aucun moteur en face pour l'instant).
    statut_execution = Column(String(30), nullable=True)   # null | en_attente | en_cours | (futur: termine/erreur)
    derniere_execution = Column(DateTime, nullable=True)    # horodatage du dernier clic "Lancer l'analyse"
    prochaine_execution = Column(DateTime, nullable=True)   # calculée pour les fréquences récurrentes (base d'un futur scheduler)
    payload_execution = Column(Text, nullable=True)         # JSON prêt à transmettre au Module 4 quand il existera
    message_execution = Column(Text, nullable=True)         # message de la dernière exécution (succès ou erreur), lisible par l'entreprise
    # Fiabilité de la dernière analyse préparée (Module 4, tâche 4.2). Le volume ne bloque
    # jamais une exécution : il la qualifie. Conservé ici pour rester affichable sur la page
    # des résultats, bien après le lancement.
    fiabilite_execution = Column(String(20), nullable=True)  # indicative | limitee | bonne
    points_execution = Column(Integer, nullable=True)        # nombre de points ayant servi à l'analyse