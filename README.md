# StockVision AI

Plateforme d'analyse et de prévision de données métier, combinant modèles statistiques et
intelligence artificielle générative, avec restitution décisionnelle via Power BI.

L'entreprise importe ses données, exprime son besoin en langage naturel, et obtient un résultat
chiffré accompagné d'une interprétation compréhensible — sans compétence statistique requise.

---

## Positionnement

La plupart des outils d'analyse imposent à l'utilisateur de savoir ce qu'il cherche et quel
modèle appliquer. StockVision AI inverse cette logique : le système inspecte les données
disponibles, détermine ce qui est analysable, sélectionne le modèle adapté, et signale
explicitement le degré de confiance à accorder au résultat.

**Le principe directeur : ne jamais affirmer plus que ce que les données permettent.**

---

## Architecture du moteur d'analyse

Trois composants aux rôles strictement séparés :

| Composant | Rôle | Ce qu'il ne fait jamais |
|---|---|---|
| Modèle de langage (LLM) | Traduit le besoin exprimé en spécification exécutable, puis interprète les résultats en français | Ne calcule aucune valeur |
| Moteur statistique / ML | Exécute le calcul et produit des valeurs vérifiables | Ne lit aucun texte libre |
| Validation Pydantic | Garantit que le moteur ne reçoit que des instructions exécutables | — |

Cette séparation garantit que les chiffres présentés reposent sur un calcul reproductible, tandis
que leur restitution reste accessible sans compétence technique.

### Chaîne de traitement

```
Extraction  →  Préparation  →  Traduction  →  Calcul  →  Interprétation  →  Restitution
   (SQL)       (pandas)        (LLM)      (ML/stats)      (LLM)          (interface + BI)
```

Chaque étape dispose de sa propre traçabilité : l'échec d'une étape n'efface jamais le résultat
des précédentes.

---

## Fonctionnalités

### Collecte et centralisation
- Import de fichiers CSV et SQL, avec validation structurelle et détection automatique des colonnes
- Connexion en lecture seule à une base externe (MySQL, PostgreSQL, SQL Server), identifiants
  chiffrés (Fernet)
- Synchronisation manuelle des connexions, historique tracé de chaque import

### Configuration de l'analyse
- Sélection granulaire des données : seules les colonnes retenues sont extraites, jamais la source entière
- Quatre objectifs d'analyse : détection de tendance, prévision d'évolution, identification
  d'anomalie, comparaison et classement
- Expression du besoin en langage naturel, traduite en spécification technique validée
- Guidage contextuel : le système indique en amont quels objectifs les données permettent, et
  pourquoi les autres ne sont pas réalisables

### Moteur d'analyse et de prévision
- Sélection automatique du modèle selon l'objectif : ARIMA, lissage exponentiel, régression
  linéaire, détection d'observations aberrantes, agrégation
- Adaptation au volume disponible : un modèle plus simple se substitue au modèle complet lorsque
  l'historique est limité, garantissant qu'une analyse reste possible sans produire de résultat
  non fondé
- Intervalle de confiance produit systématiquement, avec distinction explicite entre intervalle
  natif du modèle et estimation sur les résidus
- Indicateur de conclusivité : lorsque l'intervalle englobe la valeur de départ, le système
  refuse de qualifier le sens de l'évolution

### Restitution
- Interprétation en langage naturel, contrainte par les indicateurs : le niveau de confiance
  affiché est recalculé côté serveur et ne peut jamais excéder ce que le calcul soutient
- Le service reste fonctionnel sans accès au modèle de langage : un repli déterministe assure la
  continuité, seule la rédaction étant temporairement absente

### Alertes
- Déclenchement automatique à partir du niveau de criticité déduit des indicateurs
- Un résultat non concluant ne génère jamais d'alerte, quelle que soit l'ampleur apparente des
  chiffres
- Notification dans l'interface, consultation, marquage comme traitée, accès direct à l'analyse
  concernée

### Business Intelligence
- Sept vues SQL exposant les résultats à Power BI Desktop sous forme de colonnes plates
- Compte de base de données dédié, en lecture seule, sans aucun accès aux tables sous-jacentes
- Indicateurs calculés en SQL plutôt qu'en DAX, pour garantir la cohérence entre rapports

---

## Stack technique

| Couche | Technologies |
|---|---|
| Backend | FastAPI, SQLAlchemy, Pydantic |
| Templates | Jinja2 (rendu serveur) |
| Interface | Bootstrap 5, CSS custom, JavaScript vanilla |
| Base de données | MySQL / MariaDB |
| Analyse | pandas, NumPy, scikit-learn, statsmodels |
| IA générative | API Gemini via httpx, sortie contrainte par schéma JSON |
| Authentification | JWT en cookie httponly, bcrypt |
| Chiffrement | Fernet (cryptography) |
| Restitution | Power BI Desktop |

Architecture logique en trois tiers : présentation, application, données.

---

## Modèle de sécurité

- Authentification par JWT en cookie httponly, avec validation administrative des inscriptions
- Cloisonnement strict : chaque entreprise n'accède qu'à ses propres données
- L'administrateur supervise des volumes et des états techniques, jamais le contenu métier des
  analyses — ni le besoin exprimé, ni les données sélectionnées, ni les résultats
- Identifiants de bases externes chiffrés au repos
- Aucune donnée brute transmise au modèle de langage : la charge utile suit une liste blanche
  explicite, et non un filtre d'exclusion
- Accès Power BI restreint à des vues, sans droit sur les tables, grâce au mécanisme
  `SQL SECURITY DEFINER`

---

## Installation

```bash
git clone https://github.com/wa-ji-h/StockVision-AI.git
cd StockVision-AI

python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux / macOS

pip install -r requirements.txt
```

Créer un fichier `.env` à la racine :

```env
DATABASE_URL=mysql+pymysql://user:password@localhost/stock_vision
SECRET_KEY=<clé JWT>
FERNET_KEY=<clé de chiffrement>
LLM_API_KEY=<clé API, optionnelle>
LLM_MODEL=<identifiant du modèle>
LLM_BASE_URL=<url du service>
```

Le système est entièrement fonctionnel sans `LLM_API_KEY` : un repli déterministe prend le relais
sur les objectifs prédéfinis.

```bash
uvicorn app.main:app --reload
```

---

## Structure du projet

```
app/
├── core/          Configuration, sécurité
├── database/      Modèles SQLAlchemy
├── routes/        Endpoints FastAPI
├── schemas/       Validation Pydantic
├── services/
│   └── moteur_analyse/   Extraction, préparation, traduction, exécution, interprétation
├── static/        CSS, JavaScript
├── templates/     Vues Jinja2
└── uploads/       Fichiers importés

docs/              Documentation technique
scripts/           Migrations et outils
```

---

## Documentation

- `docs/POWERBI.md` — connexion, dictionnaire des vues, relations, visualisations suggérées

---

## Limites connues

- Le moteur ne conserve pas l'historique des tentatives d'exécution : seul l'état courant est
  connu, ce qui empêche de distinguer un échec ponctuel d'un échec répété
- L'extraction des séries longues repose sur un contournement lié à la version de MariaDB
  utilisée, avec une limite documentée et signalée dans les données exposées
- L'accès Power BI n'est pas cloisonné par entreprise : il constitue un canal d'exploitation
  interne, non destiné à être distribué

---

## Perspectives

- Exécution automatique des analyses récurrentes selon leur fréquence
- Seuils de criticité paramétrables par secteur d'activité
- Aide à la décision opérationnelle, conditionnée à un modèle de données enrichi
- Cloisonnement de l'accès décisionnel par entreprise

---

## Contexte

Projet développé dans le cadre d'un stage de fin d'études.

---

## Licence

Ce projet est distribué à des fins pédagogiques et de démonstration.