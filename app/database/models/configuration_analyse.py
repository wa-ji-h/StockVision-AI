from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base

class ConfigurationAnalyse(Base):
    __tablename__ = "ConfigurationAnalyse"

    id_configuration = Column(Integer, primary_key=True, autoincrement=True)
    objectif = Column(String(255), nullable=False)
    date_creation = Column(DateTime, server_default=func.now())
    # Module 4 — page de résultats. Sans cette date, comparer deux exécutions d'une même
    # configuration afficherait comme une évolution métier ce qui n'est qu'un changement
    # de paramétrage. Renseignée à chaque passage par /finalize.
    date_modification = Column(DateTime, nullable=True)
    id_import = Column(Integer, ForeignKey("ImportDonnee.id_import", ondelete="CASCADE"), nullable=False)
    facteurs_selectionnes = Column(Text, nullable=True)  # JSON: liste des tables/colonnes choisies (étape 1)
    frequence = Column(String(20), nullable=True)         # ponctuelle | quotidienne | hebdomadaire | mensuelle
    statut = Column(String(20), nullable=False, default="brouillon")  # brouillon | actif
    # Besoin exprimé en langage naturel (ex-precision_complementaire). Traduit en
    # spécification exécutable par app/services/moteur_analyse/traduction.py.
    besoin = Column(Text, nullable=True)

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

    # Traduction de l'intention (Module 4). `intention_reformulee` est le garde-fou :
    # ce que le système a compris, affiché à l'entreprise avant et avec le résultat.
    specification_json = Column(Text, nullable=True)
    intention_reformulee = Column(Text, nullable=True)
    # Motif d'échec de la traduction. Colonne DÉDIÉE : partager message_execution avec
    # 4.1/4.2 faisait écraser la trace au premier lancement réussi.
    intention_erreur = Column(Text, nullable=True)
    # Motif d'échec du moteur de calcul. Colonne DÉDIÉE, comme intention_erreur : chaque
    # étape écrit dans la sienne, sinon la dernière efface la trace des précédentes.
    execution_erreur = Column(Text, nullable=True)