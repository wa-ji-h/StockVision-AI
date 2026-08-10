# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

StockVision AI — a FastAPI + server-rendered (Jinja2) web app for companies to connect external
databases/files, configure analyses, and view results/alerts on a dashboard. There is an admin
side (validating company registrations, managing users) and a company ("entreprise") side
(connecting data sources, configuring analyses, viewing imports/results). `ai/` and `powerbi/`
are placeholder dirs (currently empty, `.gitkeep` only) for future analysis/reporting work.

## Commands

Activate the venv first (Windows): `venv\Scripts\activate` (or `venv\Scripts\Activate.ps1` in PowerShell).

- Install deps: `pip install -r requirements.txt`
- Run the dev server: `uvicorn app.main:app --reload`
- Create/sync DB tables from models: `python -m app.database.init_db`
- Ad-hoc schema migrations: run the relevant `migrate_*.py` script from the repo root, e.g.
  `python migrate_configuration_analyse.py`. There is no migration framework (no Alembic) —
  each `migrate_*.py` at the repo root is a standalone, idempotent script (checks
  `inspect(engine)` for existing columns/tables before altering) written for one specific schema
  change. When you add a DB column/table to a model, add a matching `migrate_*.py` script rather
  than assuming `init_db` will alter existing tables (`create_all` only creates missing tables).

There is no test suite yet (`tests/` is empty) and no lint/format config in the repo.

## Configuration

Settings load from `.env` via `app/core/config.py` (`Settings` class, instantiated once as
`settings`). Key vars: `DB_*` (MySQL via PyMySQL), `SECRET_KEY` (JWT + session signing),
`DB_ENCRYPTION_KEY` (Fernet key for encrypting third-party DB credentials stored in
`ConnexionBDD`), `ADMIN_REGISTRATION_CODE`, OAuth (`GOOGLE_CLIENT_ID`/`GITHUB_CLIENT_ID` etc.),
and SMTP (left empty in dev — emails are logged to console instead of sent, see `app/utils/email.py`).

## Architecture

**Request flow**: `app/main.py` wires up `SessionMiddleware` (for OAuth CSRF state) and mounts
five routers: `pages` (public marketing pages + signin/signup), `auth` (login/register API),
`oauth` (Google/GitHub login), `dashboard` (everything behind auth — admin and entreprise
dashboards), `password_reset`. A custom `NotAuthenticated` exception (raised by
`app/dependencies.py`) is caught by a global exception handler in `main.py` and redirects to
`/connexion` — this is the auth-gating mechanism for the whole app, not middleware.

**Auth**: JWT stored in an `access_token` cookie (not Authorization header), decoded in
`app/dependencies.get_current_user`. `require_role(RoleEnum.administrateur | .entreprise)` gates
role-specific routes. Passwords are optional on `Utilisateur` (nullable) since OAuth-created
accounts have none.

**Two-role data model**: `Utilisateur` is the base account (role, statut_compte, oauth fields).
`Entreprise` and `Administrateur` both have `idEntreprise`/`idAdmin` as a FK-as-PK back to
`Utilisateur.idUtilisateur` (shared-PK inheritance pattern) — look up the role-specific row via
that ID, not a separate lookup key. Company registrations go through
`StatutDemandeEnum` (`en_attente` → `validee`/`refusee`) before the account can log in productively.

**Dashboard router is a monolith**: `app/routes/dashboard.py` (~2200 lines) contains all
admin + entreprise dashboard routes, including third-party DB introspection
(`_build_db_url`/`_list_tables` build a SQLAlchemy URL and query `information_schema` for MySQL/
PostgreSQL/SQL Server), file uploads (`app/uploads/`), and the analysis-configuration wizard
(`_build_facteurs_selectionnes`/`_flatten_facteurs` convert between a flat UI selection like
`"table.colonne"` and the stored JSON `{"source_id": ..., "tables": {...}}` shape on
`ConfigurationAnalyse.facteurs_selectionnes`). When editing this file, search for the relevant
section rather than reading it top to bottom.

**Templates**: Jinja2 templates under `app/templates/pages/`, split into `home/` (public site),
`dashboard_admin/`, `dashboard_entreprise/`, and a shared `dashboard/base.html`. Routes set
`templates.env.globals["asset_version"]` to a timestamp per-process as a cache-buster for
`/static` assets — restart the server to pick up static asset changes in dev.

**Third-party DB connections**: `ConnexionBDD` stores encrypted credentials (via
`DB_ENCRYPTION_KEY`, Fernet) for connecting to a company's own MySQL/PostgreSQL/SQL Server
database as a *data source* to analyze — distinct from this app's own `DB_*` connection in
`app/core/config.py`.

**Company registration → login flow** (`app/services/auth_service.py`,
`app/services/entreprise_service.py`): signup creates a `Utilisateur` with no password and
`statut_compte=inactif`, plus an `Entreprise` row with `statut_demande=en_attente` — login is
rejected until an admin calls `approve_entreprise`, which generates a temp password, emails it,
sets `doit_changer_mdp=True`, and flips both `statut_compte=actif` and `statut_demande=validee`.
Admin accounts self-register via `/api/auth/register/admin` gated by `ADMIN_REGISTRATION_CODE`
(no UI for it — API only). OAuth login (`app/routes/oauth.py` + `app/services/oauth_service.py`)
shares the same cookie/JWT issuance as password login and follows the same pending-approval gate
(`HTTPException(detail="__pending__")` is the sentinel used to redirect to a "pending" message
instead of a generic error).

**Data pipeline model** (the core domain chain, all cascade-deleted top-down):
`SourceDonnee` (a company's declared source — either an uploaded file or a `ConnexionBDD`) →
`ImportDonnee` (one ingested file/sync; `meta_json` holds `{"columns": [...]}` for CSV or
`{"creates": n, "inserts": n, "tables": [...], "columns_by_table": {...}}` for SQL; uploaded
files live under `app/uploads/`, path stored in `chemin_fichier`) → `ConfigurationAnalyse`
(a saved analysis config: which tables/columns from the import, a `frequence` for recurring runs,
`statut` brouillon/actif) → `ResultatAnalyse` + `Alerte` (outputs of running that config).

**`facteurs_selectionnes` shape quirk**: the stored JSON is
`{"source_id": ..., "tables": {"<nom>": ["col", ...] | "*"}}`. For **BDD** and **SQL** sources the
keys are real table names. For a **CSV** source the keys are *column* names (the wizard lists
columns flat, there is no table level) — see the comment in `_verifier_disponibilite_facteurs`.
Any code consuming this JSON must branch on `SourceDonnee.type_source`.

**Third-party DB introspection is live, not scaffolding**: `_list_tables` in
`app/routes/dashboard.py` opens a real short-lived connection to the company's external DB
(`connect_timeout=5`, disposed immediately after) and queries `information_schema` — this runs
synchronously in a request handler, so expect it to be slow/blocking for slow or unreachable
external hosts.

---

# StockVision AI — règles projet

Plateforme d'analyse et de prévision de données d'entreprise (FastAPI + Jinja2 + MySQL).
Le contexte complet (stack, modèle de données, workflow d'authentification, design system) est
décrit dans les sections ci-dessus : **ce fichier est la référence unique**. Un ancien
`stockvision-ai-project-memory.md` est encore cité dans d'anciennes notes mais **n'existe plus** :
ne pas le chercher, ne pas s'y référer.

## Règles de travail

- Ne jamais proposer d'ajout de scope hors cahier des charges (agent conversationnel, RAG,
  vectoriel, nouveau module) sans validation explicite préalable.
- Pas de nouvelle dépendance externe sauf justification technique réelle : JS vanilla, CSS custom,
  Bootstrap 5, Jinja2. Pas de webfont CDN, pas de framework JS, pas de SDK quand httpx suffit.
- Respecter le design system existant : les tokens font foi, voir le bloc `:root` de
  `app/static/css/style.css` (fond `--bg-dark: #070b14`, accent `--accent: #22d3ee` cyan,
  dégradé `--accent-gradient` sky→cyan→indigo, police Plus Jakarta Sans), plus les conventions
  sidebar/topbar existantes.
- Confirmation modale obligatoire avant toute action destructive.
- Les tableaux et listes de l'application partagent des classes CSS communes (`.dash-status`,
  `.dash-pill`, `.dash-table-actions`, `.dash-icon-btn`) : ne pas dupliquer de styles par template.
- Avant de conclure une tâche : montrer les fichiers modifiés et un résumé court par point,
  pas un compte-rendu de session complet.
- Si un comportement semble incohérent avec le code source, vérifier d'abord que le serveur
  uvicorn a bien été redémarré (les templates Jinja2 rechargent à chaud, pas le code Python).

## Architecture du moteur d'analyse (Module 4)

Approche hybride validée, ne pas la remettre en question :
- Le calcul est fait par un modèle statistique / ML local (pandas, scikit-learn, statsmodels)
  qui produit les valeurs numériques vérifiables.
- L'interprétation en langage naturel est faite par un LLM via API externe (httpx), qui reçoit
  uniquement les résultats numériques et la précision complémentaire, jamais les données brutes.
- Un LLM ne calcule pas de prévision : il commente un résultat déjà calculé.

### Tâche 4.1 — extraction (terminée le 04/08/2026)

`app/services/moteur_analyse/extraction.py`. Point d'entrée `extraire_donnees(db, config)` →
`ResultatExtraction` (`.donnees` = un DataFrame par table ; `.dataframe` = raccourci quand une
seule table est sélectionnée). `executer_extraction(db, config)` l'enveloppe avec le suivi d'état
en base et ne lève jamais : en cas d'échec elle renvoie `None` et écrit le message dans
`ConfigurationAnalyse.message_execution`. C'est ce que `POST /configurations/{id}/lancer` appelle.

**Les trois types de sources sont de valeur égale mais n'ont pas le même support** — le
branchement sur `SourceDonnee.type_source` est explicite, sans structure supposée commune :
- `BDD` : `Table(autoload_with=engine)` puis `select()` SQLAlchemy Core sur les seules colonnes
  retenues. Les identifiants ne sont jamais concaténés dans du SQL (pas d'injection possible),
  et jamais de `SELECT *`.
- `CSV` : fichier relu depuis `app/uploads/`, `read_csv(usecols=...)`. Structure tabulaire unique.
- `SQL` : fichier relu depuis `app/uploads/`, les `INSERT` sont parsés par un tokenizer maison
  (`_lire_chaine`/`_lire_tuple`, gère `\'`, `''`, `NULL`, multi-lignes). **Un fichier importé est
  une source autonome** : ses tables sont celles déclarées dans le fichier, sans aucun rapport
  avec la base de l'entreprise. Un fichier multi-tables s'analyse table par table.

**Lecture de `facteurs_selectionnes`** : plutôt que de deviner d'après la forme du JSON (les clés
sont des colonnes pour un CSV, des tables pour BDD/SQL — voir le quirk plus haut), le lecteur CSV
confronte les deux interprétations possibles à l'en-tête réel du fichier et retient celle qui
correspond. Déterministe, auto-corrigeant, et aucune migration des configurations existantes.
La normalisation à l'écriture a donc été jugée inutile.

**Profilage du wizard** (`profiler_selection`) : réutilise les mêmes lecteurs sur un échantillon
(`ECHANTILLON_PROFIL = 200`) et renvoie, par table, le nombre de lignes exact et les colonnes
typées (`est_date`, `est_numerique`). Le typage passe par `_normaliser_types`, donc **ce que le
wizard annonce est exactement ce que l'extraction produira**. Mesuré à 41–150 ms selon la source.
Exposé par `POST /dashboard/entreprise/configurations/source/{id}/profil`, qui y ajoute
l'évaluation de compatibilité de chaque objectif (`_evaluer_objectifs` dans `dashboard.py`).

**Garantie** : aucune donnée non sélectionnée n'est extraite, même temporairement.
**Garde-fou** : `LIMITE_LIGNES = 100_000`, tronqué avec avertissement.
**Statuts** écrits par ce module : `extraction_en_cours` (badge bleu), `erreur_donnees` (badge
rouge). Le succès repasse en `en_attente` — 4.2 n'existe pas encore, aucun badge inventé.

### Tâche 4.2 — préparation (terminée le 05/08/2026)

`app/services/moteur_analyse/preparation.py`. `preparer_donnees(resultat, config)` →
`ResultatPreparation` (`.tables` = une `TablePreparee` par table ; `.table` = raccourci mono-table,
même contrat que `ResultatExtraction.dataframe`). `executer_preparation(db, config, resultat)`
enveloppe avec le suivi d'état et ne lève jamais. Branché après 4.1 dans `/lancer`.

**Constantes centralisées** : `MINIMUM_POINTS = 20` et `OBJECTIFS_TEMPORELS` vivent ici. Les
règles `temporel`/`min_points` de `_OBJECTIFS` (dashboard.py) en sont **dérivées par une boucle**,
jamais recopiées — le wizard promet donc exactement ce que 4.2 acceptera.

**Détection temporelle** : `extraction.colonnes_temporelles()`, partagée avec le profilage du
wizard. Parmi plusieurs colonnes de date, la mieux renseignée est retenue.

**Pipeline** : nettoyage (colonnes vides écartées, lignes sans date écartées, fuseau retiré) →
tri chronologique → agrégation `resample` selon `frequence` (`quotidienne→D`, `hebdomadaire→W`,
`mensuelle→MS`) → contrôle de volume.

Décisions de conception :
- `sum(min_count=1)` à l'agrégation : une période sans donnée reste `NaN` et ne devient pas un
  faux zéro. La grille régulière est conservée (4.3 en a besoin) mais seules les périodes
  porteuses comptent comme points — d'où `n_points` (30) ≠ `n_periodes` (175).
- L'agrégation temporelle ne s'applique **qu'aux objectifs temporels** : sur un classement elle
  détruirait la granularité par produit/client qui fait toute l'analyse.
- `PreparationError` hérite d'`ExtractionError` pour garder le même contrat de message et rester
  rattrapable par les appelants qui gèrent déjà 4.1.
- Le contrôle de volume de 4.2 porte sur les points **après agrégation**, celui du wizard sur les
  lignes brutes : 30 lignes étalées sur 6 mois passent le wizard et sont rejetées par 4.2 au pas
  mensuel. C'est voulu, les deux vérifications ne mesurent pas la même chose.

### Le volume ne bloque plus (05/08/2026)

`app/services/moteur_analyse/fiabilite.py` — **définition unique** des niveaux, importée par
`extraction.py`, `preparation.py` et `dashboard.py` (module sans dépendance interne : aucun cycle).

`NIVEAUX_FIABILITE` : `<10 → indicative`, `10-19 → limitée`, `≥20 → bonne`. `MINIMUM_POINTS` a
disparu, ainsi que `min_points` sur `_OBJECTIFS`.

**Seule l'impossibilité mathématique interrompt une analyse** : objectif temporel sans colonne de
date, sans colonne numérique, ou données vides. Le volume ne bloque plus jamais — il qualifie la
fiabilité, stockée en base (`fiabilite_execution`, `points_execution` +
`migrate_configuration_analyse_fiabilite.py`) pour rester affichable sur la page des résultats
bien après le lancement.

`points_par_frequence()` compte les périodes réellement porteuses à chaque pas de temps, sur la
colonne de date **entière** (un décompte doit être exact, contrairement au typage qui échantillonne).
Le wizard s'en sert pour annoncer, sous chaque bouton de fréquence, les points obtenus et la
fiabilité correspondante — sans jamais désactiver une fréquence.

⚠️ La section « résultats » du dashboard est encore un placeholder (`placeholder.html`) : la
fiabilité y est **prête en base** mais pas encore affichée. À brancher quand la page existera (4.3).

### Compléments du 05/08/2026 (retours d'usage)

- **Règles de compatibilité** : un objectif temporel exige une date **et** une mesure numérique,
  toutes deux dans la **même table** (une date ici et un nombre là ne font pas une série).
  `_evaluer_objectifs` expose `a_mesure`, `analysable`, `colonnes_mesure` en plus du reste.
- **Notification intégrée** : `svNotify(message, 'ok'|'ko'|'info')`, définie une fois dans
  `dashboard/base.html`, disponible sur toutes les pages du dashboard. Les 16 `alert()` du projet
  ont été remplacés. Les erreurs tiennent 9 s et sont refermables, les confirmations 3 s.
  Le toast local `#cfgToast` de `configurations_list` a été supprimé au profit du partagé.
- **Exécutions bloquées** : `executer_extraction` écrit `extraction_en_cours` en base *avant* de
  lire la source. Si la requête meurt (redémarrage `uvicorn --reload`, onglet fermé, délai),
  le badge bleu restait figé pour toujours. `_reparer_executions_bloquees()` convertit au rendu
  de la liste tout `extraction_en_cours` de plus de `DELAI_EXECUTION_BLOQUEE` (5 min) en erreur
  explicite. À revoir si le Module 4 se dote d'un worker asynchrone : le délai ne tiendra plus.

Pièges rencontrés, à ne pas réintroduire :
- prendre le *dernier* import d'une source au lieu de celui que la configuration désigne
  (`config.id_import`) fait analyser le mauvais fichier après un réimport ;
- `data-detail` du popup est rendu côté serveur : toute mise à jour AJAX du statut doit aussi
  réécrire cet attribut (`majDetail()`), sinon le popup affiche l'état d'avant le lancement ;
- un `ImportDonnee.chemin_fichier` à NULL (imports antérieurs à `migrate_import_donnee_chemin.py`)
  rend la source inanalysable : il faut réimporter le fichier.

## État d'avancement

- Module 0 (site vitrine) : terminé
- Module 1 (authentification et gestion des accès) : terminé
- Module 2 (importation et connexion aux données) : terminé
- Module 3 (configuration d'analyse) : terminé le 04/08/2026 — wizard 3 étapes avec sélection
  granulaire des colonnes, CRUD complet des configurations, filtres combinables, statut
  d'exécution, bouton "Lancer l'analyse" produisant payload_execution sans moteur en face.
  Complété le 05/08/2026 : objectif « Comparer et classer », guidage contextuel, objectifs
  conditionnés à la compatibilité réelle des données (voir journal)
- Module 4 (moteur d'analyse et de prévision IA) : en cours
  - tâche 4.1 (extraction des données sélectionnées) : terminée le 04/08/2026, validée sur les
    trois types de sources (CSV, fichier SQL mono et multi-tables, connexion BDD)
  - tâche 4.2 (préparation) : terminée le 05/08/2026 — détection temporelle partagée avec le
    wizard, nettoyage, agrégation par fréquence, contrôle de volume centralisé
  - tâche 4.3 (modèles statistiques) : prochaine étape — consomme `ResultatPreparation` sans
    réécriture (`.table.donnees` est déjà indexé par le temps, trié, agrégé)
- Module 5 (alertes) : non commencé
- Module 6 (Power BI) : non commencé

## Journal des sessions

### 05/08/2026 — Module 3 : objectif « Comparer et classer » + guidage contextuel

Fait :
- `optimisation_ressource` remplacé par `comparaison_classement` (violet conservé, icône
  `arrows-sort`). **Aucune migration** : zéro configuration en base ne portait l'ancien objectif ;
  `_OBJECTIF_FALLBACK` absorbe le cas d'un dump ancien restauré.
- `_OBJECTIFS` porte désormais les règles de compatibilité (`temporel`, `min_points`) ; le drapeau
  `recommended` figé a été retiré — la recommandation est **calculée** (date + ≥20 points →
  prévision, sinon comparaison).
- `profiler_selection()` dans `extraction.py` + endpoint `POST /configurations/source/{id}/profil`.
  La détection de date vient du dtype pandas réel, jamais d'une heuristique sur le nom de colonne.
- Bandeau de guidage : **macro Jinja unique** `wiz_guide()` appelée aux trois étapes, pilotée par
  `majGuide()`. Remplace `.wiz-panel-note` (supprimé, pour ne pas laisser un style parallèle).
  Variantes `.wiz-guide--info/--ok/--warn`, même famille visuelle que `.imh-modal-note`.
- Cartes objectif à trois états (`.is-recommended` / `.is-compatible` / `.is-unavailable`).
- `_diagnostiquer_objectif()` : après une extraction réussie, vérifie que l'objectif reste tenable
  et produit un message qui **guide** (ce qui manque + quelle action débloque).

Piège à ne pas réintroduire :
- estomper une carte indisponible avec `opacity` **sur la carte** plafonne aussi l'opacité de la
  raison, qui doit rester lisible : l'estompage porte sur `.wiz-obj-icon/-title/-desc`
  individuellement. Une opacité > 1 ne compense rien, elle est bornée à 1.

Reste :
- Source CSV de démo créée pour la validation : `source 16` / `import 21` (`ventes_2026.csv`,
  30 lignes). Suppression : `DELETE FROM SourceDonnee WHERE id_source = 16;` (cascade).

### 04/08/2026 — Module 4, tâche 4.1 (extraction)

Fait :
- `app/services/moteur_analyse/` créé (`extraction.py` + `__init__.py`), branché sur
  `POST /dashboard/entreprise/configurations/{id}/lancer` qui ne consommait pas son payload.
- Colonne `ConfigurationAnalyse.message_execution` + `migrate_configuration_analyse_message.py`.
- Deux états de badge « Exécution » : « Extraction en cours » (`.dash-status-info`, bleu, ajouté
  au composant existant) et « Erreur — données indisponibles » (`.dash-status-ko`).
- Refonte du popup de détail partagé par `configurations_list`, `imports_history` et
  `admin_imports` : variante `.imh-modal-card--lg`, croix positionnée en absolu, grille de méta
  encadrée, encart de message teinté `.imh-modal-note--ok/--ko`, badge de statut en en-tête.
- `test_extraction.py` à la racine : test manuel (`python test_extraction.py <id>`, `--commit`
  pour écrire en base).

Décisions :
- Extraction **synchrone** dans la requête (aucun scheduler/worker en scope) — même compromis
  que `_list_tables`. À revoir si le Module 4 se dote d'un worker.
- Pas de normalisation de `facteurs_selectionnes` : la lecture déterministe suffit et évite de
  réécrire les configurations déjà en base.
- Succès → `en_attente` plutôt qu'un nouveau statut, tant que 4.2 n'existe pas.

Reste / dettes :
- Sources « codes promo » (import 2) et « client.sql » (import 3) ont `chemin_fichier` à NULL :
  inanalysables tant qu'elles ne sont pas réimportées.
- Données de démo créées pour la validation : sources 13/14, imports 17/18, configs 25/26.
  Suppression : `DELETE FROM ConfigurationAnalyse WHERE id_configuration IN (25,26);`
  puis `DELETE FROM SourceDonnee WHERE id_source IN (13,14);` (cascade).
- `CLAUDE.md` n'est pas suivi par git — penser à `git add CLAUDE.md`.
