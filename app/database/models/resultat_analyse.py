from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base

class ResultatAnalyse(Base):
    __tablename__ = "ResultatAnalyse"

    id_resultat = Column(Integer, primary_key=True, autoincrement=True)
    date_execution = Column(DateTime, server_default=func.now())
    valeur_analyse = Column(Text)
    valeur_prevue = Column(Text)
    type_analyse = Column(String(100))
    id_configuration = Column(Integer, ForeignKey("ConfigurationAnalyse.id_configuration", ondelete="CASCADE"), nullable=False)

    # Module 4 — exécution. Le modèle appliqué est conservé pour que l'entreprise sache
    # sur quelle méthode repose son analyse : ARIMA et régression linéaire ne se valent pas.
    modele_applique = Column(String(50), nullable=True)
    fiabilite = Column(String(20), nullable=True)          # indicative | limitee | bonne
    intervalle_bas = Column(Text, nullable=True)
    intervalle_haut = Column(Text, nullable=True)
    # Structure complète (séries, classement, anomalies) prête pour la visualisation
    # et l'interprétation, sans réécriture.
    resultat_json = Column(Text, nullable=True)

    # Module 4 — tâche 4.6 : criticité, base de déclenchement des alertes (Module 5).
    # Colonnes dédiées et non un simple champ dans resultat_json : le Module 5 doit
    # pouvoir filtrer en SQL (`criticite_rang >= 2`) sans désérialiser chaque résultat.
    criticite = Column(String(20), nullable=True)        # normal | attention | eleve | critique
    criticite_rang = Column(Integer, nullable=True)      # 0..3, pour trier et comparer
    criticite_motif = Column(Text, nullable=True)        # pourquoi ce niveau, en une phrase

    # Module 4 — tâche 4.5 : restitution en langage naturel. Ces colonnes sont
    # facultatives par construction : quand le service externe est indisponible, les
    # colonnes numériques ci-dessus restent renseignées et consultables, seule
    # l'interprétation rédigée manque — avec son motif dans interpretation_erreur.
    interpretation = Column(Text, nullable=True)            # la synthèse, en une phrase
    interpretation_json = Column(Text, nullable=True)       # la réponse structurée complète
    interpretation_source = Column(String(20), nullable=True)   # llm | indisponible
    interpretation_erreur = Column(Text, nullable=True)