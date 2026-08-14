from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.database.connection import Base


class Alerte(Base):
    """Module 5 — projection d'un résultat critique en alerte consultable.

    ⚠️ **La relation a changé : Résultat → Alerte, et non plus Configuration → Alerte.**
    Le modèle initial rattachait l'alerte à la configuration avec `unique=True`, ce qui
    interdisait plus d'une alerte par configuration *à vie* — intenable pour une analyse
    récurrente — et empêchait de remonter à l'exécution qui l'avait déclenchée. Une
    configuration reste jointe pour le filtrage et l'affichage, sans contrainte d'unicité.

    Une alerte ne reformule rien : `message` reçoit `ResultatAnalyse.criticite_motif` tel
    quel, et `niveau` son code de criticité. Aucun appel LLM n'intervient ici.
    """

    __tablename__ = "Alerte"

    id_alerte = Column(Integer, primary_key=True, autoincrement=True)
    type_alerte = Column(String(100))          # type d'analyse : prevision, anomalie…
    message = Column(Text)                     # criticite_motif, repris sans réécriture
    niveau = Column(String(50))                # eleve | critique
    date_creation = Column(DateTime, server_default=func.now())

    # Un résultat produit au plus une alerte : relancer une configuration ne doit pas en
    # fabriquer en double. La création est donc un « créer sinon mettre à jour ».
    id_resultat = Column(
        Integer,
        ForeignKey("ResultatAnalyse.id_resultat", ondelete="CASCADE"),
        unique=True,
        nullable=True,       # nullable : les lignes antérieures au Module 5 n'en ont pas
    )
    # Conservée pour filtrer et afficher l'objectif concerné sans double jointure.
    # `unique=True` retiré : une configuration récurrente alerte à chaque exécution.
    id_configuration = Column(
        Integer,
        ForeignKey("ConfigurationAnalyse.id_configuration", ondelete="CASCADE"),
        nullable=False,
    )

    # Rang recopié depuis la criticité : permet « toutes les alertes critiques » en SQL
    # sans joindre ResultatAnalyse.
    criticite_rang = Column(Integer)

    # Une alerte traitée reste consultable, mais sort du décompte des non lues.
    statut = Column(String(20), default="nouvelle")     # nouvelle | traitee
    date_traitement = Column(DateTime, nullable=True)

    __table_args__ = (
        # Le décompte des non lues est calculé à chaque rendu de page du dashboard :
        # il doit rester constant même quand la table grossit.
        Index("ix_alerte_statut_date", "statut", "date_creation"),
        Index("ix_alerte_configuration", "id_configuration"),
    )
