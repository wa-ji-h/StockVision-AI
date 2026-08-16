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
  faux zéro. La grille régulière est conservée (4.4 en a besoin) mais seules les périodes
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
fiabilité y est **prête en base** mais pas encore affichée. À brancher quand la page de
résultats existera.

### Tâche 4.3 — traduction de l'intention (LLM) (terminée le 06/08/2026)

Couche intermédiaire entre la préparation (4.2) et l'exécution (4.4).
`app/services/moteur_analyse/` :
`schema_analyse.py` (vocabulaire + schéma Pydantic fermé + repli déterministe) et
`traduction.py` (appel HTTP via httpx + validation).

**Répartition des rôles, non négociable :**
- **LLM** : traduit l'intention en spécification, puis interprétera le résultat. Ne calcule jamais.
- **Moteur ML/statistique** : exécute la spécification, produit les chiffres. Ne lit jamais de
  texte libre.
- **Pydantic** : garantit que le moteur ne reçoit que des instructions exécutables.

**Vocabulaire fermé — 4 opérations, et rien d'autre** : `classement`, `prevision`, `tendance`,
`anomalie`. Chaque objectif prédéfini correspond à l'une d'elles (`TYPE_PAR_OBJECTIF`), ce qui
garantit la compatibilité des configurations existantes.

**Schéma** : union discriminée sur `type_analyse`, `extra="forbid"` partout. Toute clé inventée
est rejetée — une tentative d'injecter `executer_sql` échoue à la validation, rien n'est exécuté.

**Double validation, volontairement séparée** :
1. `IntentionAnalysee` — forme, énumérations, bornes.
2. `valider_colonnes()` — chaque colonne citée existe **et** a le bon type, confronté au profil
   de 4.2. Aucune seconde source de vérité.

**Contrainte d'API à connaître** : la sortie structurée n'accepte pas `minimum`/`maximum`/
`minLength`/`maxLength` et exige `additionalProperties: false` + un `required` exhaustif sur
chaque objet. `schema_json()` nettoie donc le schéma Pydantic avant l'envoi ; **Pydantic revalide
ces bornes côté serveur**. C'est exactement pourquoi la validation est en deux niveaux.

**Configuration** (`.env`) — voir la section « Fournisseur de modèle de langage » ci-dessous
pour le détail de chaque variable :
```
LLM_PROVIDER=gemini
LLM_API_KEY=          # vide = repli déterministe, aucun appel réseau
LLM_MODEL=gemini-3.1-flash-lite
LLM_BASE_URL=         # vide = URL par défaut du fournisseur
```
`LLM_API_KEY` vide n'est pas une panne : le système reste **entièrement démontrable** sans clé.
Toute erreur (clé absente, timeout, refus, JSON non conforme, colonne inventée) bascule sur la
spécification par défaut de l'objectif prédéfini. L'analyse ne s'interrompt que si **aucun
objectif n'a été choisi** — il n'y a alors rien à appliquer.

**Garde-fou** : `reformulation` est obligatoire au niveau du schéma (10–300 car.). Stockée dans
`ConfigurationAnalyse.intention_reformulee`, elle est destinée à être affichée deux fois — à la
traduction puis avec le résultat.

**Renommage** : `precision_complementaire` → `besoin` (`migrate_configuration_analyse_besoin.py`,
`CHANGE COLUMN`, données préservées ; 0 ligne portait une valeur). Colonnes ajoutées :
`specification_json`, `intention_reformulee`.

### Fournisseur de modèle de langage — Gemini (08/08/2026)

**Google AI Studio (Gemini) est le fournisseur par défaut**, pour son palier gratuit. Anthropic
reste disponible : le basculement est une variable d'environnement.

`app/services/moteur_analyse/llm_client.py` est **le seul fichier qui sait quelle API est
appelée**. URL, en-tête d'authentification, forme de la requête, extraction du texte, détection
d'un refus, adaptation du schéma : tout y est. `traduction.py` (4.3) et `interpretation.py`
(4.5) n'en connaissent que `appeler_llm(systeme, invite, schema, max_tokens, delai)` et
traduisent `LLMIndisponible` dans leur propre vocabulaire d'erreurs. Vérifié : ni `httpx`, ni
`x-api-key`, ni `settings` n'apparaissent plus dans ces deux modules.

**Variables `.env`** :

| Variable | Rôle |
|---|---|
| `LLM_PROVIDER` | `gemini` (défaut) ou `anthropic`. Un nom inconnu retombe sur le défaut. |
| `LLM_API_KEY` | Vide = **repli déterministe, aucun appel réseau**. Le système reste démontrable. |
| `LLM_MODEL` | `gemini-3.1-flash-lite` par défaut ; `claude-opus-5` côté Anthropic. |
| `LLM_BASE_URL` | **Facultatif.** Vide, chaque fournisseur applique la sienne. |

Différences d'API, mesurées contre l'API réelle et non déduites de la documentation :

| | Gemini | Anthropic |
|---|---|---|
| Endpoint | `POST {base}/v1beta/models/{modele}:generateContent` | `POST {base}/v1/messages` |
| Authentification | en-tête `x-goog-api-key` | en-tête `x-api-key` + `anthropic-version` |
| Consigne système | `systemInstruction.parts[].text` | champ `system` |
| Sortie structurée | `generationConfig.responseJsonSchema` + `responseMimeType` | `output_config.format` |
| Schéma envoyé | **le schéma Pydantic tel quel** | bornes retirées, `additionalProperties: false` ajouté |
| Texte de la réponse | `candidates[0].content.parts[].text` | premier bloc `type: "text"` |
| Refus | `promptFeedback.blockReason`, ou `finishReason` ∈ SAFETY/RECITATION/… | `stop_reason: "refusal"` |

⚠️ **`responseJsonSchema`, pas `responseSchema`.** L'autre champ, calqué sur OpenAPI, rejette
`$defs` et `$ref` par un 400 — or notre union discriminée en produit. Vérifié :
`Unknown name "$defs" at 'generation_config.response_schema'`.

⚠️ **Gemini accepte `maxLength` mais ne l'applique pas.** Constaté en test : une `synthese` de
plus de 300 caractères est passée par la contrainte de l'API et a été **rejetée par Pydantic**.
Les limites de longueur sont donc énoncées dans le prompt en plus du schéma. La contrainte du
fournisseur est une aide, jamais la garantie — c'est exactement pourquoi la double validation
ne dépend d'aucun fournisseur. Second cas observé : `type_analyse: "classification"`, hors du
vocabulaire fermé, rejeté de la même façon.

⚠️ **Le quota gratuit est par modèle et se compte en journée.** `gemini-2.5-flash` est plafonné
à **20 requêtes/jour** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), en plus d'une
limite par minute — bien trop peu pour développer. Basculer de modèle libère du quota, puisqu'il
est compté séparément par modèle : changer `LLM_MODEL`, rien d'autre. Le 429 est traité comme
n'importe quel échec : repli déterministe, message conservé.

⚠️ **L'API ne renvoie aucun compteur de quota restant** — ni en-tête, ni endpoint. Deux moyens
seulement : la page https://ai.dev/rate-limit, ou le 429 lui-même, qui nomme la limite atteinte
et son `quotaId`. Inutile de chercher à l'afficher dans l'application.

**Le défaut est `gemini-3.1-flash-lite`**, choisi sur mesure et non sur catalogue :

| Modèle | Interprétation complète | Verdict |
|---|---|---|
| **`gemini-3.1-flash-lite`** | **2,5 s** | retenu |
| `gemini-3-flash-preview` | 10,2 s | fonctionne |
| `gemini-3.5-flash` | 20,4 s | fonctionne, mais lent en démonstration |
| `gemini-2.5-flash` | 22,3 s | **échoue** — dépasse la longueur de `recommandation` deux fois de suite, reprise comprise |

⚠️ **Un 503 n'est pas un dépassement de quota.** `This model is currently experiencing high
demand` est une surcharge passagère côté Google : le même appel repasse quelques minutes plus
tard. Ne pas le confondre avec le 429.

**Délais d'appel portés à 60 s des deux côtés** (4.3 comme 4.5). À 30 s, une traduction valide
expirait et retombait sur le repli déterministe alors que rien n'avait échoué : `gemini-3.5-flash`
met 20 à 27 s, dont l'essentiel en raisonnement avant d'écrire.

**Vouvoiement imposé explicitement dans les deux prompts.** « À la deuxième personne » ne
tranchait pas entre `tu` et `vous` : `gemini-3.1-flash-lite` tutoyait (« Tu souhaites
identifier… ») alors que toute l'application vouvoie.

### Longueurs : marge de sécurité et seconde tentative (08/08/2026)

Symptôme : `interpretation_erreur` sur la config 68 — « le champ `limite` : 455 caractères pour
400 au maximum ». Gemini n'applique pas `maxLength`, la validation rejette, **l'entreprise perd
toute son interprétation** alors que le fond était bon.

**Le dépassement est stochastique, pas systématique.** Reconstitué avec l'ancienne consigne :
`limite` sort à 309/400 — juste sous le plafond. Avec la consigne à marge : 181/400. C'est ce
frôlement qui explique un rejet intermittent plutôt que constant.

Trois corrections, dans l'ordre où elles agissent :

1. **Marge annoncée** — `MARGE_LONGUEUR = 0.75`. Le prompt annonce 75 % de la limite validée
   (`synthese` 225 pour 300, `lecture` 675 pour 900, `recommandation`/`limite` 300 pour 400,
   `points_cles` 3 pour 4). Les cibles sont **calculées depuis `InterpretationRedigee`**
   (`_limites_max()` lit `model_fields`) : la consigne ne peut pas diverger du schéma.
2. **Détail exploitable** — `valider_reponse` dit désormais *quel champ, de combien*
   (« 455 caractères pour 400 au maximum ») au lieu du message Pydantic brut, et **journalise
   le contenu rejeté**. Sans cela le contenu était perdu : c'est pour ça que le rejet de la
   config 68 n'a pas pu être exhumé, seulement reconstitué.
3. **Seconde tentative** — `FormatInvalide` (sous-classe d'`InterpretationInvalide`) distingue
   « la réponse est arrivée mais déborde » de « le transport a échoué ». Sur le premier cas
   seulement, `_prompt_correctif()` renvoie la demande en nommant le champ, sa taille produite
   et la cible. **Une seule reprise** : deux échecs de format d'affilée renoncent, avec le
   motif « — après une seconde tentative. » Une panne réseau ne déclenche aucune reprise :
   redemander poliment ne répare pas une coupure.

La reprise est tracée dans `ResultatInterpretation.ajustements`, au même titre qu'un plafond de
confiance rabattu — l'entreprise voit que sa première réponse a été refaite.

Même marge côté 4.3 : `reformulation` est annoncée à 220 caractères pour 300 validés. Pas de
reprise là-bas : le repli déterministe fournit déjà une spécification exécutable, la perte est
sans commune mesure.

`MAX_TOKENS` est passé à 4000 dans les deux couches : les modèles à raisonnement consomment une
part du budget de sortie **avant** d'écrire, et une limite trop basse rend une réponse vide avec
`finishReason: MAX_TOKENS` — cas explicitement détecté et message dédié.

### Tâche 4.4 — exécution, le moteur de calcul (terminée le 06/08/2026)

`app/services/moteur_analyse/execution.py`. `executer_calcul(intention, table, config_id)` →
`ResultatExecution` ; `executer_et_stocker(db, config, intention, prepare)` enveloppe avec
journalisation et persistance, et ne lève jamais.

**Le modèle de prévision dépend de la fiabilité calculée en 4.2** (`MODELE_PAR_FIABILITE`) —
une analyse reste possible quel que soit le volume, sans jamais être plus assurée que les
données ne le permettent :

| Fiabilité | Modèle | Intervalle de confiance |
|---|---|---|
| bonne (≥20 pts) | ARIMA (statsmodels, ordre 1,1,1) | **natif** (`get_forecast().conf_int()`) |
| limitée (10-19) | Lissage exponentiel de Holt | estimé sur les résidus (±1,96 σ) |
| indicative (<10) | Régression linéaire (scikit-learn) | estimé sur les résidus |

`methode_intervalle` vaut `"modele"` ou `"residus"` : l'origine de l'intervalle est explicite,
jamais présentée comme équivalente. Si un modèle ne converge pas, **repli sur la régression
linéaire avec un avertissement** plutôt qu'un échec.

Les quatre opérations : `classement` (groupby + agrégation + tri + limite ; `mesure=None` →
comptage), `prevision`, `tendance` (moindres carrés → sens, pente, R², seuil relatif à
l'échelle des valeurs), `anomalie` (z-score ; seuils `SEUIL_PAR_SENSIBILITE` :
faible 3,0 · moyenne 2,5 · forte 2,0). **Les filtres sont appliqués avant tout calcul**, avec
des opérateurs issus d'une énumération fermée — aucune expression n'est évaluée.

**Indicateurs structurés — `ResultatExecution.indicateurs`.** Chaque opération expose ses
métriques en **données**, jamais en texte : l'interprétation et l'affichage les lisent sans rien
parser. Tous portent un booléen `conclusif` :

| Opération | `conclusif` vaut faux quand | Clés |
|---|---|---|
| prevision | l'intervalle **englobe la valeur de départ** → `sens: "indetermine"` | `sens`, `intervalle_contient_depart`, `largeur_intervalle`, `largeur_relative`, `variation_pct` |
| tendance | `r2 < 0.5` — la droite explique mal les données | `sens`, `pente`, `r2`, `variation_pct` |
| anomalie | aucune observation aberrante détectée | `nb_anomalies`, `seuil_ecarts_types`, `moyenne`, `ecart_type` |
| classement | moins de 2 éléments — classer un seul élément ne classe rien | `nb_elements`, `premier`, `dernier`, `ecart_premier_dernier` |

⚠️ **Sans cet indicateur, l'interprétation présenterait une incertitude comme une tendance.**
Cas réel : cfg 58 prévoit 29,56 depuis 25,00 avec un intervalle [17,09 ; 42,03] — soit +18 % en
apparence, alors que le modèle ne distingue pas une hausse d'une baisse. `largeur_relative`
(0,84 ici : l'intervalle est presque aussi large que la valeur) quantifie cette incertitude.

**Branchement** : `/lancer` exécute le calcul en **étape 7**, après la préparation. La
spécification est relue depuis `specification_json` via `intention_depuis_resume()` — donc
**aucun appel LLM au lancement**, et Pydantic revalide au passage. Un échec du calcul n'invalide
pas extraction et préparation : la réponse reste `ok: true` avec `execution: null` et le motif
dans `execution_erreur`.

**Stockage** : `ResultatAnalyse` étendu (`modele_applique`, `fiabilite`, `intervalle_bas/haut`,
`resultat_json`) + `ConfigurationAnalyse.execution_erreur`, colonne **dédiée** comme
`intention_erreur` (`migrate_execution_resultat.py`). `resultat_json` porte les séries de
visualisation, prêtes pour l'interprétation et l'affichage sans réécriture.

**Dépendances ajoutées** : `statsmodels==0.14.6` (+ `patsy`, `packaging`). `scikit-learn` et
`scipy` étaient déjà présents. `pip install -r requirements.txt` après un `git pull`.

### Tâche 4.5 — restitution en langage naturel (terminée le 07/08/2026)

`app/services/moteur_analyse/interpretation.py`. `interpreter_resultat(resultat, objectif,
besoin, reformulation)` → `ResultatInterpretation` ; `interpreter_et_stocker(db, config,
resultat)` enveloppe avec journalisation et persistance, et ne lève jamais. Branché en
**étape 8** de `/lancer`, uniquement si le calcul a produit un résultat — il n'y a rien à
commenter autrement. Aucune dépendance ajoutée (httpx + Pydantic déjà présents).

**La frontière des données est une liste blanche, pas un filtre.** `charge_utile()` énumère
ce qui part vers le service externe ; ce qui n'y figure pas ne part pas. L'inverse — exclure
les champs sensibles — laisse toujours passer ce qu'on a oublié d'exclure le jour où
`ResultatExecution` gagne un champ.

| Transmis | Jamais transmis |
|---|---|
| valeurs calculées, intervalle + `methode_intervalle` | toute ligne de la source |
| `indicateurs` (dont `conclusif`), fiabilité, modèle | les points de `serie_historique` |
| agrégats de tête, plafonnés à `MAX_ELEMENTS_TRANSMIS` (5) | le détail des observations aberrantes |
| cardinalité et bornes de l'historique (`nb_points`, `debut`, `fin`) | les colonnes non retenues |

Les libellés d'agrégats (nom du produit en tête d'un classement) **sont** transmis : ce sont
des résultats de regroupement, pas des enregistrements — sans eux l'interprétation d'un
classement ne pourrait rien nommer. Le plafond de 5 borne l'exposition sans dégrader la
lecture.

**Le plafond de confiance est vérifié, pas demandé.** `InterpretationRedigee.niveau_confiance`
(`eleve`/`modere`/`faible`) est recalculé côté serveur par `confiance_maximale()` :
`conclusif is False` → `faible` ; fiabilité `bonne` → `eleve` autorisé ; sinon `modere`. Si le
modèle annonce mieux que ce plafond, on le rabat et on trace l'ajustement dans
`ResultatInterpretation.ajustements`. Le prompt énonce déjà la règle — ce contrôle en fait une
garantie. Même principe qu'en 4.3 : ce qui compte n'est pas ce qu'on demande au modèle, c'est
ce qu'on accepte de lui. Le plafond ne **rehausse** jamais une confiance annoncée basse.

**Format de réponse fermé** (`extra="forbid"`), donc enregistrable sans relecture humaine :
`synthese` (la réponse en une phrase), `lecture`, `points_cles` (1–4), `recommandation`,
`limite`, `niveau_confiance`. `limite` est **obligatoire** au niveau du schéma, pour la même
raison que `reformulation` l'est en 4.3 : sans elle, une incertitude se lit comme un fait.
`nettoyer_schema()` (extrait de `schema_json()` en 4.3) retire les bornes que l'API refuse ;
Pydantic les revalide à la réception.

**Aucun repli rédigé — et c'est délibéré.** Contrairement à 4.3 qui replie sur une
spécification par défaut, l'indisponibilité du service ne produit ici aucun texte : les
colonnes numériques restent renseignées et consultables, `interpretation_source` passe à
`indisponible` et le motif est conservé. En 4.3 le repli permet de *calculer* ; ici une
interprétation par gabarit serait indiscernable d'une vraie lecture. **Une analyse sans
commentaire reste utilisable ; une analyse mal commentée ne l'est pas.**

**Stockage** : 4 colonnes sur `ResultatAnalyse` (`interpretation`, `interpretation_json`,
`interpretation_source`, `interpretation_erreur`) + `migrate_resultat_interpretation.py`.
Toutes nullables par construction — c'est ce qui rend le point précédent vrai en base. La
règle « chaque étape écrit dans sa propre colonne » s'applique : 4.5 ne touche jamais à
`message_execution` (4.1/4.2), `intention_erreur` (4.3) ni `execution_erreur` (4.4).

**`ResultatExecution.id_resultat`** est renseigné par `executer_et_stocker` après le commit :
c'est par lui que 4.5 retrouve la ligne à annoter, sans la rechercher. Un appel à
`executer_calcul` seul le laisse à `None` — 4.5 le détecte, journalise et n'écrit rien plutôt
que d'annoter la mauvaise ligne.

Validé le 07/08/2026 (`test_interpretation.py --tout`, 3/3) : frontière (6 contrôles, dont
« aucune clé hors liste blanche » et « aucun point de série transmis »), plafond de confiance
(5 cas), indisponibilité. Chaîne complète relue en base sur cfg 58 (prévision ARIMA,
`conclusif: false`) et cfg 59 (classement) : chiffres présents, `interpretation_source =
indisponible`, motif conservé.

### Régression corrigée : `est_mesure` absent du diagnostic de lancement (10/08/2026)

Symptôme : une configuration « Prévoir une évolution » sur une source CSV portant `date` et
`prix` était **annoncée compatible dans le wizard puis refusée au lancement** — « vos données
portent une date, mais aucune valeur chiffrée à suivre dans le temps ».

Cause : **deux producteurs de profil**. `profiler_selection` (wizard) passait par
`decrire_colonnes` et renseignait `est_mesure` ; `_diagnostiquer_objectif` (lancement)
reconstruisait un profil à la main avec seulement `est_date` et `est_numerique`. Depuis que
`_evaluer_objectifs` lit `est_mesure`, ce second profil renvoyait toujours `None` → falsy →
`tables_analysables` vide → **tout objectif temporel devenait irréalisable au lancement**,
quelles que soient les données et quel que soit le type de source.

Ce n'était donc pas le décalage table/colonne des sources fichier : `prix` et `qte` étaient
bien détectés comme mesures. Reproduit sur les sources 23 et 24 avant correction.

Correctif : `_decrire_colonnes` devient **`decrire_colonnes`**, publique et exportée, et
`_diagnostiquer_objectif` l'utilise. Un seul producteur de description, donc plus de
divergence possible. Vérifié sur trois cas : date + `prix` (aucun blocage), date sans grandeur
(blocage), date + identifiant seul (blocage).

⚠️ **Règle qui en découle : tout profil confronté à `_evaluer_objectifs` doit venir de
`decrire_colonnes`.** Recomposer un profil « juste avec les champs utiles » est exactement ce
qui a produit ce bug — les champs utiles changent.

Nuance connue : au lancement les DataFrames n'ont plus de schéma, donc les clés primaires et
les booléens déclarés ne sont plus détectables (seuls le nom et les valeurs le sont). Le
diagnostic est donc un peu plus permissif que le wizard — dans le sens sûr.

### Guidage en langage métier (07/08/2026)

Le wizard affichait `20 pts · bonne` sous chaque bouton de fréquence : un vocabulaire
statistique qui ne dit rien à une entreprise. **Les points et la fiabilité restent calculés
et stockés** — 4.4 en dépend pour choisir le modèle, 4.5 pour calibrer la confiance — ils ne
sont simplement plus le message affiché.

`adequation_frequence(n_points)` dans `fiabilite.py`, **dérivée de `NIVEAUX_FIABILITE`** et
non d'une seconde échelle : une fréquence est adaptée exactement quand sa fiabilité est bonne.

| Points | Code | Affiché |
|---|---|---|
| ≥ 20 | `adaptee` | **rien** — pas de bruit visuel quand tout va bien |
| 2–19 | `peu_adaptee` | « Vos données couvrent une période courte — à ce rythme, le résultat sera peu précis. » |
| < 2 | `impossible` (`bloquant: true`) | « Vos données ne couvrent qu'une seule période à ce rythme. Choisissez une fréquence plus fine pour que l'analyse soit réalisable. » |

`MINIMUM_PERIODES = 2` : en dessous il n'y a pas de série, rien à comparer ni à projeter.

**Le blocage est signalé au choix de la fréquence, pas au lancement.** C'est exactement le cas
documenté plus haut (30 lignes étalées sur 6 mois passent le wizard et sont rejetées par 4.2 au
pas mensuel) — l'entreprise le voit désormais *avant* de valider. Le bandeau de guidage bascule
en `warn` et le message prime sur le décompte d'objectifs indisponibles. **Aucune fréquence
n'est désactivée et l'enregistrement n'est pas bloqué** : le message est un signalement
actionnable, pas une barrière, conformément à « le volume ne bloque plus jamais ».

**Le signalement ne s'applique qu'aux analyses temporelles.** L'agrégation par fréquence ne
concerne que les objectifs temporels (voir 4.2) : sur un classement le pas de temps ne réduit
rien, et annoncer « une seule période » serait une fausse alerte. `analyseTemporelle()` lit
`objectif.temporel`, déjà transmis par `_evaluer_objectifs`. Sans objectif choisi (besoin
libre) le message reste affiché — la traduction peut produire une analyse temporelle.

⚠️ **Cette disparition doit s'expliquer, sinon l'interface se transforme sans raison visible.**
Sur un objectif non temporel, la note ne se vide pas : elle affiche
`NOTE_FREQ_NON_TEMPORELLE` — « Pour cet objectif, la fréquence détermine seulement le rythme de
mise à jour, pas la précision du résultat. » La raison technique est juste, mais elle n'existe
pas pour l'entreprise tant qu'on ne la lui dit pas. **Une phrase qui enseigne vaut mieux qu'un
vide.**

**Répartition de l'affichage** : marqueur court sur le bouton (`peu précis` / `non réalisable`),
phrase entière sous la ligne pour la fréquence **sélectionnée**. Quatre phrases complètes sous
quatre boutons côte à côte seraient illisibles.

**Cartes objectif — même principe** : `_BLOCAGES` porte un couple *(constat, action)*, exposé
en deux champs distincts (`raison`, `resolution`) et rendu sur deux lignes (`.wiz-obj-reason`,
`.wiz-obj-fix`). « Nécessite une colonne de date » devient « Vos données ne contiennent aucune
colonne de date. » + « Choisissez un import qui porte une date (commande, facture, mouvement de
stock), ou ajoutez la colonne de date à votre sélection. » Nommer la contrainte sans dire quoi
faire laissait l'entreprise dans l'impasse.

Supprimé au passage : `.wiz-obj-reason--bonne/limitee/indicative` et
`.wiz-freq-hint--bonne/limitee/indicative`, styles morts depuis que la fiabilité ne s'affiche
plus sur les cartes.

### Le bandeau oriente, il ne constate pas (10/08/2026)

Complète la règle « une phrase » ci-dessous : la phrase doit en plus **servir à décider**.

- **Étape 2** — ce que la sélection permettra, ou ce qui manque *et* l'action qui débloque.
  Registre : « Votre sélection permet toutes les analyses proposées. » / « Votre sélection ne
  contient aucune valeur chiffrée : ajoutez une quantité, un montant ou un total pour
  débloquer la prévision et la tendance. » Un cas distinct a été ajouté pour la date et la
  mesure présentes mais **dans des tables différentes** — sans les deux au même endroit, il
  n'y a pas de série à tracer.
- **Étape 3** — quel objectif et quel rythme conviennent : « Avec ces données,
  « Prévoir une évolution » est l'objectif le plus adapté, au rythme hebdomadaire. »
  L'objectif vient de l'état `recommande` déjà calculé par `_evaluer_objectifs` ;
  `frequenceConseillee()` retient **le pas le plus large qui reste adapté** — agréger au mois
  parle davantage qu'agréger au jour, tant qu'il reste assez de périodes porteuses — et à
  défaut le plus fin encore réalisable. Sur un objectif non temporel, la phrase dit que la
  fréquence ne fait qu'y rythmer la mise à jour, pour qu'on ne cherche pas « la bonne »
  fréquence sans raison.

### Modal de détail : messages avant sélection (10/08/2026)

Deux défauts de lisibilité corrigés dans `configurations_list.html` :

- **Ordre** — besoin et reformulation remontent **avant** « Données sélectionnées ». Le détail
  de la sélection poussait auparavant les messages d'état sous la ligne de flottaison, alors
  que ce sont eux qui appellent une réaction.
- **Compacité** — une ligne par table, nom et colonnes sur le même flux (`.cfg-sel-ligne`), au
  lieu d'un bloc par table avec un badge « Toutes les colonnes » sous chaque nom. Trois tables
  suffisaient à imposer un défilement. « toutes les colonnes » est désormais du **texte** et
  non un badge : en badge, la mention passait pour une colonne. Au-delà de 8 colonnes, un
  compteur `+N` porte la liste entière en `title` — la hauteur est bornée sans rien cacher.

### Le bandeau tient en une phrase (08/08/2026)

Règle applicable aux trois étapes : **`majGuide()` n'affiche qu'une phrase.** Le paramètre
`points`, la `<ul class="wiz-guide-list">` de la macro et son CSS ont été supprimés — un
bandeau qui énumère les colonnes de date, les mesures, les identifiants écartés et le nombre
de lignes est un rapport technique, pas un guidage. L'entreprise doit y lire une seule chose :
peut-elle continuer, et sinon quoi faire.

- **Cas nominal** : « Vos données permettent toutes les analyses proposées. » Aucun nom de
  colonne.
- **Cas problématique** : ce qui manque **et** l'action, en une phrase. Le détail par objectif
  vit déjà sur les cartes (`raison` + `resolution`) — le bandeau ne le répète pas.

**Le détail est journalisé, pas affiché** : `_log.info("[profil] source … dates=… mesures=…
identifiants ecartes=… oui/non ecartes=… tables vides=…")` dans l'endpoint de profilage. Il
reste consultable en console uvicorn pour comprendre après coup pourquoi une colonne n'a pas
été retenue comme mesure, sans encombrer l'interface.

### Colonnes non mesurables et tables vides — détectées à l'étape 2 (08/08/2026)

Deux échecs qui ne se révélaient qu'au lancement remontent maintenant au moment de la
sélection, là où l'entreprise peut encore corriger.

**Un identifiant n'est pas une mesure.** Le système avait retenu `idAdmin` comme grandeur à
analyser pour une détection d'anomalie. `est_numerique` reste le fait de typage brut ;
**`est_mesure`** est ce qui décide qu'une colonne est analysable. Les deux ne diffèrent que
sur les identifiants, et c'est `est_mesure` que consomme le reste de la chaîne
(`_evaluer_objectifs`, `valider_colonnes`, `specification_par_defaut`).

Trois critères **objectifs et indépendants** (`extraction.py`), un seul suffit :

1. `nom_evoque_identifiant()` — le nom commence ou finit par « id », **séparateur ou casse
   faisant foi** : `id`, `id_client`, `idAdmin`, `clientId` oui ; `idee`, `rigide`, `valide`
   non. Un test de sous-chaîne nu confondrait les deux.
2. `valeurs_forment_une_cle()` — valeurs **entières**, strictement uniques *et* croissantes.
   La condition d'entier n'est pas décorative : sans elle, une colonne de montants qui se
   trouve triée dans le fichier serait écartée — exactement la colonne à analyser. Une clé
   auto-incrémentée est toujours entière, la restriction ne coûte aucun vrai positif.
   `MINIMUM_VALEURS_CLE = 5` : en dessous, « unique et croissant » arrive par hasard.
3. Clé primaire déclarée au schéma — `table.primary_key` pour une BDD, `_cles_primaires_sql()`
   pour un fichier SQL (contrainte de table **et** déclaration en ligne). Un CSV n'a pas de
   schéma : seuls les critères 1 et 2 s'y appliquent.

**Un indicateur oui/non non plus.** `doit_changer_mdp` en `TINYINT(1)` est numérique au sens
du dtype : on ne prévoit pas un drapeau et une anomalie de drapeau ne veut rien dire. Il décrit
un **état**, donc une dimension. Deux sources, la déclaration primant la déduction :

- **schéma** — `booleens_declares_bdd()` (type `Boolean` SQLAlchemy, ou `TINYINT` de largeur 1 :
  MySQL n'a pas de type booléen, `BOOL` y est un alias et c'est la largeur qui porte
  l'intention) ; `booleens_declares_sql()` pour un fichier (`BOOL`, `BOOLEAN`, `BIT(1)`,
  `TINYINT(1)`, en découpant sur les virgules **hors parenthèses** pour ne pas casser un
  `DECIMAL(10,2)`) ;
- **contenu** — `colonne_est_booleenne()` : dtype booléen, ou numérique ne valant que 0 et 1 sur
  au moins `MINIMUM_VALEURS_BOOLEEN = 5` valeurs. La restriction à {0, 1} est volontaire : une
  taille qui ne prend que 5 et 10 reste une grandeur.

Le schéma n'est pas redondant avec le contenu : `utilisateur` ne compte que 2 lignes, trop peu
pour conclure quoi que ce soit — c'est `TINYINT(1)` qui tranche. Un CSV n'ayant pas de schéma,
seule la déduction s'y applique.

**Identifiants et booléens restent utilisables comme dimension** — grouper par identifiant ou
par indicateur garde du sens ; c'est seulement comme mesure qu'ils n'en ont aucun.
`specification_par_defaut` retient d'ailleurs un booléen comme dimension à défaut de colonne
texte. Le prompt de traduction les annonce explicitement (« identifiant / indicateur oui/non —
utilisable comme dimension, jamais comme mesure ») : la validation les rejetterait de toute
façon, autant ne pas provoquer un repli évitable.

**Tables vides** : `resume.tables_vides` est calculé à partir du décompte que le profilage
connaît déjà. Une table vide fait échouer l'extraction au lancement (« Aucune donnée
exploitable dans "alerte" ») — elle est désormais nommée à l'étape 2, avec l'action à mener.

**Aucune mesure après exclusion** : `_CLES_SANS_MESURE` choisit le constat selon ce qui a
réellement été écarté — identifiants, indicateurs oui/non, les deux, ou aucun des deux
(`sans_mesure`, le cas « pas la moindre colonne chiffrée »). Nommer la mauvaise catégorie
ferait douter l'entreprise de la détection. Les colonnes écartées sont listées même quand tout
va bien, pour la même raison.

Validé sur la base réelle (source 8, `stock_vision`, 11 tables) : `administrateur` → `idAdmin`
écarté, aucune mesure restante ; `utilisateur` → `doit_changer_mdp` écarté par le schéma
(2 lignes seulement, la déduction ne pouvait pas conclure) ; `alerte` et `passwordresettoken`
signalées vides ; clés étrangères (`id_import`, `id_source`, `id_admin_validateur`) écartées
elles aussi ; vraies mesures conservées (`taille_octets`, `points_execution`, `valeur_analyse`,
`port`).

⚠️ Faux positif connu et accepté : une colonne d'entiers métier qui serait triée et sans
doublon dans les 200 premières lignes (un stock strictement croissant, par exemple) est
écartée des mesures par le critère 2. Le nom et le schéma la rattrapent rarement — si le cas
se présente, c'est le critère 2 qu'il faudra restreindre, pas les trois.

### Tâche 4.6 — indicateurs de suivi et criticité (terminée le 08/08/2026)

Une partie existait déjà : 4.4 produisait `variation_pct`, `largeur_relative`, `conclusif`,
`r2`, `pente`, `ecart_premier_dernier`… Le recensement a précédé l'écriture, pour ne pas
dupliquer sous un autre nom.

**Indicateurs ajoutés** — clés communes aux quatre opérations, jamais préfixées par type :
`moyenne`, `ecart_type`, `nb_observations` (`_stats_communes`, `ddof=0`, comme la détection
d'anomalie qui les produisait déjà — **noms repris, pas doublés**), `ecart_absolu`,
`erreur_ajustement`, `concentration` (classement), `ecart_max` (anomalie).

⚠️ **`erreur_ajustement` ≠ `variation_pct`.** L'un est la dispersion des résidus — de combien le
modèle se trompe sur ce qu'il a déjà vu. L'autre mesure l'évolution d'un bout à l'autre. Deux
questions distinctes ; les confondre ferait passer un modèle mal ajusté pour une forte variation.
Les trois `_prevoir_*` renvoient désormais leur σ résiduel (5ᵉ élément du tuple) ; pour ARIMA le
premier résidu est écarté, il suit la différenciation et n'a pas de sens.

**Criticité** — `criticite.py`, **définition unique des seuils**, sur le modèle de `fiabilite.py`.
Module sans dépendance interne. Calcul entièrement déterministe : **aucun appel LLM**, une alerte
doit être rejouable et redonner le même verdict.

Quatre niveaux, avec un rang numérique pour que le Module 5 filtre par comparaison :
`normal` (0) · `attention` (1) · `eleve` (2) · `critique` (3).

| Opération | Indicateur déclencheur | attention / élevé / critique |
|---|---|---|
| prévision, tendance | `\|variation_pct\|` | 10 % · 25 % · 50 % |
| anomalie | taux `nb_anomalies / nb_observations` | 2 % · 5 % · 10 % |
| anomalie (2ᵉ axe) | `ecart_max / seuil_ecarts_types` | 1,0 · 1,3 · 1,6 |
| classement | `concentration` | 50 % · 70 % · *(jamais)* |

L'anomalie retient le **maximum des deux axes** : dix écarts légers et un seul très fort méritent
tous deux d'être remontés ; un seul axe en manquerait la moitié.

**Trois règles structurantes, validées avant écriture :**

1. **`conclusif == false` → `normal`, sans exception.** Pas un plafond : le niveau *est* `normal`,
   testé avant tout le reste. L'incertitude n'est pas un signal.
2. **La fiabilité plafonne, elle ne pousse jamais** : `indicative` → 1, `limitee` → 2, `bonne` → 3.
3. **Plafonds de contexte** : une prévision dont `largeur_relative > 1,0` est ramenée à
   `attention` (l'ampleur existe mais n'est pas exploitable) ; un classement ne dépasse jamais
   `eleve` — il décrit un état, pas un écart à une attente.

Un plafonnement n'est signalé **que lorsqu'il rabat effectivement** un niveau : les seuils de
concentration s'arrêtent d'eux-mêmes à `eleve`, `PLAFOND_PAR_TYPE` y est une garantie, pas un
correctif, et n'apparaît donc pas dans `plafonnements`.

**Stockage** — colonnes dédiées sur `ResultatAnalyse` (`criticite`, `criticite_rang`,
`criticite_motif`) + index sur le rang, via `migrate_resultat_criticite.py`. Le détail complet
(indicateur déclencheur, valeur, seuil franchi, plafonnements) vit dans `resultat_json` **et** est
remonté à la racine de `ResultatExecution.resume()`. Motif : le Module 5 doit pouvoir écrire
`WHERE criticite_rang >= 2` sans désérialiser chaque ligne. Vérifié en SQL.

**`recalculer_criticite.py`** (racine) rattrape l'existant, en deux modes complémentaires — la
distinction n'est pas cosmétique :

- **`--recalcul`** (défaut) — recalcule la criticité des lignes **déjà en base**, depuis leur
  `resultat_json`. Aucune relecture des sources, aucun appel LLM : la criticité est une fonction
  pure des indicateurs, donc rejouable à l'identique. Les indicateurs manquants (`concentration`,
  `ecart_max`) sont reconstitués depuis les séries de visualisation stockées, jamais inventés ;
  `_concentration` est **importée de 4.4**, pas recopiée, sinon le verdict divergerait d'un
  lancement normal. Les deux clés `ecart_zscore` / `ecart_type` sont acceptées, pour ne pas
  ignorer les lignes antérieures au renommage.
- **`--relancer`** — rejoue 4.1 → 4.2 → 4.4 et **écrit une nouvelle ligne**. Seul moyen d'obtenir
  le bloc statistique complet (`moyenne`, `ecart_type`, `erreur_ajustement`…), qui exige de
  relire les données. 4.5 n'est pas appelée : elle consommerait du quota LLM sans rien apporter
  à la criticité.

⚠️ **Relancer ne renseigne pas les lignes anciennes** — `executer_et_stocker` en crée une
nouvelle, il ne corrige pas la précédente. C'est `--recalcul` qui rattrape l'historique. Les deux
sont sans effet de bord si on les relance, et n'écrivent rien sans `--commit`.

Exécuté le 08/08/2026 : 8 lignes recalculées, puis 5 configurations relancées (1 échec —
cfg 60, un seul point exploitable ; 2 sautées — cfg 52 et 65, sans spécification). Résultat :
**13 lignes, aucune sans criticité**, 10 `normal` et 3 `attention`.

**Piège corrigé au passage** : les observations aberrantes portaient la clé `ecart_type` pour le
z-score de *l'observation*, homonyme de l'indicateur global `ecart_type` de la *série*.
`charge_utile` lisait déjà `ecart_zscore` — **l'ampleur des anomalies n'est donc jamais parvenue à
l'interprétation depuis 4.5**. Clé renommée en `ecart_zscore`, seul consommateur à corriger :
`test_execution.py`.

`test_criticite.py` : règle absolue, seuils de variation, plafonds, double axe d'anomalie,
complétude de la justification, plus une matrice variation × fiabilité (`--matrice`).

### Page « Résultats & prévisions » (10/08/2026)

`GET /dashboard/entreprise/resultats` + `resultats.html`. Remplace `placeholder.html` et rend
enfin visible tout ce que la chaîne 4.1 → 4.6 produisait sans que personne ne le voie.

⚠️ **Déclarée AVANT `/dashboard/entreprise/{section}`** : FastAPI apparie dans l'ordre de
déclaration, la route générique l'absorberait sinon.

**Une carte par exécution**, triées par date décroissante, la plus récente dépliée. Ordre interne
volontaire — du plus décisif au plus discret : demande rappelée → indicateurs clés → graphique →
interprétation → pied technique.

`_presenter_objectif` a été **remontée au niveau module** : elle était imbriquée dans la route des
configurations alors que CLAUDE.md la désigne comme source unique. Une configuration se reconnaît
désormais à l'identique sur les deux pages, icône et couleur comprises.

**Le rappel de la demande est le 2ᵉ emplacement du garde-fou** (le 1er est le modal de détail des
configurations). Le **besoin exprimé prime** sur la reformulation : c'est la question posée par
l'entreprise, pas ce que le système en a compris. La reformulation prend le relais seulement quand
aucun besoin libre n'a été saisi.

**Indicateurs clés adaptés à l'opération** (`_indicateurs_cles`) : prévision → observé / prévu /
intervalle / fiabilité ; tendance → départ / arrivée / évolution + R² ; anomalie → nombre /
moyenne / écart-type + seuil ; classement → tête / part du total / éléments comparés. La 4ᵉ tuile
est toujours la fiabilité, teintée des couleurs de `fiabilite.py`.

⚠️ **Un résultat non concluant n'affiche jamais sa variation comme chiffre clé** — « sens non
déterminé » à la place — et porte un bandeau ambre avant tout chiffre. Sans cela l'interface
affirmerait ce que le calcul refuse de soutenir, exactement ce que `conclusif` sert à éviter.

**Graphiques : SVG écrit à la main**, aucune bibliothèque. Courbe (observé plein / prévu
pointillé / bande d'intervalle), barres horizontales pour un classement, points aberrants en
couleur de statut avec anneau de surface. Le survol passe par des cibles élargies portant un
`<title>` natif : une couche d'interaction sans une ligne de dépendance. Dessinés **à
l'ouverture** de la carte — mesurer un conteneur replié donnerait une largeur nulle — et
redessinés au redimensionnement.

Couleurs : `#22d3ee` observé (accent), `#818cf8` projeté, `#f87171` anomalie. La paire
observé/projeté échoue la bande de luminosité du validateur de palette mais passe CVD (ΔE 15,0),
vision normale (19,8) et contraste ; elle est **doublée d'un encodage secondaire** (trait plein vs
pointillé) et d'une légende, ce qui rend l'identité indépendante de la couleur. Écart assumé pour
rester sur les tokens de la marque.

**Historique — approche retenue** : une carte par résultat (pas de regroupement, qui aurait cassé
le tri chronologique), plus deux ajouts légers :
- marqueur de filiation `Exécution 3 sur 3` et lien `?config=<id>` pour isoler une lignée ;
- puce d'évolution sur la grandeur comparable du type (`_COMPARABLE_PAR_TYPE`). Sur un classement,
  **le changement de tête prime** sur le delta chiffré : « Produit C a remplacé Produit A » est
  l'information métier.

⚠️ **Quatre cas où aucun delta n'est affiché** — un delta faux est pire que pas de delta :
l'une des deux exécutions non concluante ; type d'analyse changé ; fréquence changée (pas
d'agrégation différent) ; **configuration modifiée entre les deux**. Ce dernier cas a motivé
`ConfigurationAnalyse.date_modification` (`migrate_configuration_analyse_modification.py`),
horodatée dans `/finalize` — seul point par lequel une configuration change. Sans elle, un
changement de paramétrage passerait pour une évolution métier : c'est précisément ce qu'on
observait sur cfg 59 (concentration 41,7 → 51,8 → 41,7 %).

**Filtres** par niveau de criticité, comptes calculés **avant** filtrage — un filtre doit annoncer
ce qu'il cache. Un niveau à zéro reste affiché, grisé : son absence est une information.

### Colonne « Objectif » et configurations « besoin libre » (06/08/2026)

Une configuration peut n'avoir qu'un besoin exprimé (`objectif == ""`). La colonne affichait
alors une case vide : ligne impossible à identifier.

`_presenter_objectif(config)` dans `dashboard.py` est la **source unique** de cet affichage
(liste, modal, filtres) :
- objectif renseigné → libellé de l'objectif, besoin en sous-ligne discrète (`.cfg-obj-besoin`) ;
- besoin seul → le besoin en principal, **en italique** (`.cfg-obj-name--libre`) pour le
  distinguer d'un intitulé, avec l'icône/couleur déduites de `specification_json`
  → `specification.type_analyse` → `_OBJECTIF_PAR_TYPE` ;
- ni l'un ni l'autre → « Sans objectif ni besoin ».

**Troncature en CSS, pas côté serveur** : `text-overflow: ellipsis` + `min-width: 0` (sans ce
dernier, l'ellipsis ne s'applique pas dans un conteneur flex). L'attribut `title` porte donc
toujours le texte intégral au survol.

**Filtre `besoin_libre`** (`_FILTRE_BESOIN_LIBRE`) : sans cette catégorie, les configurations
sans objectif prédéfini disparaissaient silencieusement dès qu'un filtre d'objectif était actif.
Vérifié : 6 configurations = 1 + 2 + 3 selon les filtres, aucune perdue.

Piège corrigé au passage : la macro `obj_icon` de `configurations_list.html` rendait encore
l'ancienne icône « adjustments » (curseurs) pour `arrows-sort` — seule celle du wizard avait été
mise à jour lors du remplacement d'objectif. **Les deux macros doivent rester identiques.**

### Traçabilité de la traduction (06/08/2026) — trois défauts corrigés

Constat : `specification_json` et `intention_reformulee` restaient NULL sur toutes les
configurations. Trois causes distinctes, toutes réelles :

1. **Aucune journalisation.** Le `except Exception: return None` avalait tout sans trace.
   → `logging.getLogger("stockvision.moteur_analyse")` : `INFO` au début et au succès,
   `WARNING` sur échec métier, `exception()` sur erreur inattendue. Visible en console uvicorn.
2. **La trace d'échec partageait `message_execution` avec 4.1/4.2**, donc le premier
   lancement réussi l'écrasait et l'échec devenait invisible.
   → colonne **dédiée** `intention_erreur` (`migrate_configuration_analyse_intention_erreur.py`).
3. **La traduction n'était jamais retentée.** Les configurations antérieures à la couche, ou
   celles dont la traduction avait échoué (clé API ajoutée depuis), restaient NULL pour
   toujours. → `/lancer` retente quand `intention_reformulee` est vide.

Règle qui en découle : **`message_execution` appartient à 4.1/4.2, jamais à la traduction.**
Chaque étape écrit dans sa propre colonne, sinon la dernière efface les précédentes.

### Interface de la traduction (06/08/2026)

- **Étape 3 du wizard** : le champ devient « Que souhaitez-vous savoir précisément ? »
  (`#wizBesoin`), avec 4 exemples **cliquables** (`.wiz-besoin-exemple`) qui remplissent le
  champ. Règle « objectif **ou** besoin » validée aux deux bouts : `checkStep3Ready()` côté
  client (le bouton reste désactivé, la note passe en ambre) et `422` côté serveur dans
  `/finalize`. La validation serveur est la seule qui fasse autorité.
- **La traduction a lieu à la finalisation**, pas au lancement : `_traduire_et_stocker()`
  remplit `specification_json` + `intention_reformulee` dès l'enregistrement. L'entreprise voit
  donc ce qui a été compris **avant d'avoir lancé quoi que ce soit** — c'est le sens du
  garde-fou. Cette fonction ne lève jamais : une traduction indisponible n'empêche pas
  d'enregistrer une configuration par ailleurs valide.
- **Modal de détail** : bloc « Ce que nous avons compris » (`.imh-modal-note--info`). Quand la
  traduction n'a pas abouti, le bloc bascule en « Interprétation indisponible » et affiche le
  motif — sans quoi rien ne distinguerait « pas encore traduit » de « mal compris ».
- **Duplication** : recopie `besoin`, `specification_json` et `intention_reformulee`. À objectif,
  besoin et sélection identiques la spécification l'est aussi — inutile de refaire un appel LLM.
- **Modification** : passe par la même route `/finalize`, donc revalidation et **retraduction**
  automatiques (le besoin a pu changer).

Piège : `ConfigurationAnalyse.objectif` est `nullable=False`. Une configuration sans objectif
stocke `""`, jamais `NULL` — un script qui insère `objectif=None` échoue sur la contrainte.

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

## Module 5 — Alertes (10/08/2026)

### ⚠️ Correction du modèle de données — à répercuter dans le diagramme de classes

```
AVANT :  ConfigurationAnalyse  1 ──── 1     Alerte      (unique=True sur id_configuration)
APRÈS :  ResultatAnalyse       1 ──── 0..1  Alerte      (unique=True sur id_resultat)
         ConfigurationAnalyse  1 ──── 0..n  Alerte      (index simple, aucune unicité)
```

Deux défauts rendaient le modèle initial inutilisable :

1. **`unique=True` sur `id_configuration`** interdisait plus d'une alerte par configuration
   **à vie**. Or une analyse récurrente doit alerter à chaque exécution critique : la deuxième
   insertion levait une `IntegrityError`.
2. **L'alerte ne savait pas de quelle exécution elle parlait.** Rattachée à la configuration,
   elle ne pouvait pas renvoyer vers le bon résultat — donc ni le bon graphique, ni le bon
   intervalle, ni la bonne interprétation.

`migrate_alerte_resultat.py` ajoute `id_resultat`, `criticite_rang`, `statut`,
`date_traitement`, retire l'unicité sur `id_configuration` et pose l'unicité sur `id_resultat`.
`type_alerte`, `message`, `niveau` et `date_creation` gardent leur rôle.

⚠️ **Piège MySQL rencontré** : `DROP INDEX` échoue (erreur 1553) tant qu'une clé étrangère
s'appuie sur l'index. La séquence est : retirer la contrainte → supprimer l'index unique →
créer l'index simple → rétablir la contrainte.

### Déclenchement

`app/services/alertes.py` — Module 5, ne dépend que de `criticite.py` (module feuille, aucun
cycle). **Seuil dérivé, jamais recopié** : `SEUIL_ALERTE = RANG_PAR_NIVEAU["eleve"]`. Déplacer
« élevé » dans `criticite.py` déplace le déclenchement avec lui.

Rangs 0 et 1 → rien. Alerter sur une variation mineure noierait l'entreprise, et une alerte
qu'on n'ouvre plus ne vaut rien.

**Aucune reformulation, aucun appel LLM** : `message` reçoit `criticite_motif` tel quel,
`niveau` le code de criticité. Le motif a été produit de façon déterministe par 4.6, il est
déjà l'explication du déclenchement.

**Branché dans `executer_et_stocker`** (4.4), après le commit du résultat — le seul point où
un résultat naît. Le placer dans la route laisserait sans alerte tout ce qui passe par un
script (`recalculer_criticite.py --relancer`). Import différé pour que le Module 4 ne dépende
pas du Module 5 au chargement.

`enregistrer_alerte` **crée, met à jour ou retire** selon le rang courant : un résultat
repassé sous le seuil voit son alerte disparaître, sans quoi l'interface afficherait un verdict
que le calcul ne soutient plus. Ne lève jamais — une alerte manquante ne doit pas faire échouer
une analyse par ailleurs réussie.

### Interface

**Cloche du topbar** — badge chiffré et panneau déroulant. Alimentés par un **context processor
Starlette** (`_contexte_alertes`), pas par les routes : recopier le décompte dans ~15
`TemplateResponse` garantissait qu'une route oubliée afficherait un compteur faux. Chaque entrée
mène au résultat qui l'a produite — c'est tout l'intérêt du rattachement au résultat.

**Page « Mes alertes »** (`/dashboard/entreprise/alertes`, déclarée avant le catch-all
`{section}`) : filtres par niveau avec comptes calculés avant filtrage, marquage « traitée » en
POST. Une alerte traitée **reste listée** mais sort du décompte — barrée et estompée, jamais
masquée.

**Vue admin** (`/dashboard/admin/alertes`) : volumes par entreprise et par niveau.
⚠️ **Aucun message, aucun motif, aucun lien vers un résultat** — vérifié par un test dédié sur
les champs exposés. L'administrateur supervise la charge du parc, il n'agit pas à la place des
entreprises.

### Supervision administrateur (12/08/2026)

`app/services/supervision.py` — l'administrateur ne suit pas les analyses, il veille sur le
parc. Le module n'agrège que des **faits déjà produits par l'application**, et chaque entrée
mène à l'écran où il agit. Aucun nouvel événement n'est fabriqué, aucune table ajoutée.

Trois natures, distinguées par ce que l'administrateur peut en faire — c'est cette distinction
qui rend le flux lisible :

| Nature | Source (déjà existante) | Rôle | Écran |
|---|---|---|---|
| **Action requise** | `Entreprise.statut_demande == en_attente` | il tranche | `/dashboard/admin/demandes` |
| **Intervention possible** | `SourceDonnee.statut` ∈ erreur, suspendu | il peut corriger | `/dashboard/admin/imports` |
| **À surveiller** | alertes de rang 3 non traitées | il observe | `/dashboard/admin/alertes` |

⚠️ **« À surveiller » ne donne jamais accès au détail métier.** Une alerte critique se compte et
se situe — quelle entreprise, combien — elle ne se lit pas. Le détail appartient à l'entreprise
qui l'a produite, et le libellé le dit explicitement pour qu'aucun administrateur ne croie qu'on
attend de lui une action sur l'analyse.

`activite_recente()` complète par le fil de vie du parc : imports déposés, configurations créées.
Qui a fait quoi et quand, jamais le contenu.

**Une cloche, deux significations.** Le context processor sert les deux rôles : l'entreprise voit
ses alertes non traitées (chacune menant à son résultat), l'administrateur ce qui appelle son
attention (chacun menant à son écran d'action). Le badge admin porte l'accent de la marque et non
le rouge : côté administrateur ce sont surtout des décisions à prendre, pas des incidents.

**Page admin réorganisée** : ce sur quoi il peut agir en tête, puis les volumes, puis la
répartition par entreprise (barre de proportion plutôt que deux nombres à comparer mentalement)
et le fil d'activité. L'ancien tableau plat ne hiérarchisait rien.

⚠️ **Styles `.dash-alertes-*` supprimés** au profit de `.dash-cloche-*` : deux styles parallèles
pour le même composant est précisément ce que le design system interdit.

### Page « Entreprises partenaires » — supervision par l'activité (12/08/2026)

La page affichait l'identité (nom, secteur, email, inscription) et **rien sur l'usage** :
impossible de distinguer une entreprise qui travaille d'une qui s'est inscrite et n'a jamais rien
fait. Email et date d'inscription sont donc **descendus dans le modal** — ce ne sont pas des
critères de supervision — et les colonnes portent désormais des volumes : sources (avec le nombre
en erreur), analyses lancées (avec le nombre du mois), alertes non traitées, dernière activité.

**Aucune table, aucune colonne ajoutée.** Tout se déduit de `SourceDonnee`, `ImportDonnee`,
`ConfigurationAnalyse`, `ResultatAnalyse` et `Alerte`.

**`activite_par_entreprise(db, debut_mois)`** dans `supervision.py` : **4 requêtes agrégées,
quel que soit le nombre d'entreprises** — chaque décompte est un `GROUP BY idEntreprise` sur tout
le parc, jamais une requête par ligne (ce qui aurait donné 4 × N). Vérifié en instrumentant le
moteur SQLAlchemy : 4 requêtes exactement, et les volumes confrontés à un décompte naïf ligne à
ligne (6 sources / 4 analyses / 2 alertes sur l'entreprise 7, identiques).

⚠️ **Il n'existe aucune date de dernière connexion** sur `Utilisateur`. « Dernière activité » est
donc `max(dernier import, dernière exécution d'analyse)` : une entreprise qui relance une analyse
sans réimporter est active, et réciproquement. Affichée en ancienneté (« Il y a 3 jours »), la
date exacte en sous-ligne — un délai se lit plus vite qu'une date à soustraire.

**Le liseré latéral ne signale que ce qui appelle un regard** : orange `#fb923c` pour une source
en erreur ou suspendue, rouge `#f87171` pour une alerte critique non traitée. **Une entreprise
dormante n'en porte aucun** — `DELAI_DORMANCE = 30 jours` produit une mention grise (« Dormante »,
« Inscrite, jamais utilisée »), jamais une couleur d'alerte. *L'inactivité n'est pas un incident*,
et la traiter visuellement comme tel apprendrait à ignorer le liseré.

Même raison pour la tuile « Sources en difficulté » : à zéro elle passe en gris (`--calme`), pas
en vert. Le vert dirait « bravo » là où il n'y a qu'une absence de problème — il reste réservé aux
confirmations de succès.

**Action unique : l'œil.** Un seul bouton par ligne, ouvrant le modal de supervision. Aucune
suspension, aucune modification : l'administrateur supervise, il n'intervient pas sur l'activité
d'une entreprise depuis cette page.

**Modal** — l'ordre reprend celui du modal de détail des configurations : le **message d'état
d'abord** (un seul, celui qui prime : alerte critique > source en erreur > jamais utilisée >
dormante > rien à signaler), puis les volumes, puis l'identité, puis le rappel de périmètre. Le
détail de la sélection avant les messages d'état est exactement le défaut corrigé le 10/08.

**Périmètre vérifié par un test**, pas seulement par relecture : le `data-detail` rendu ne contient
ni motif, ni message, ni résultat, ni interprétation — uniquement des nombres. Une note en pied de
tableau le rappelle, comme sur la page des alertes.

### Socle commun : modal et carte d'indicateur (12/08/2026)

Deux composants dupliqués dans les deux dashboards ont été **remplacés par un socle unique**.
Aucune couche de compatibilité : les anciennes familles sont **supprimées**, pas doublées — deux
styles parallèles finissent toujours par diverger.

**`.sv-modal-*`** — 7 modals réécrits dessus : détail d'import, de source, de configuration,
d'entreprise, et les 3 confirmations. Remplace `.imh-modal-*` (nommée d'après `imports_history`,
la page qui l'avait vue naître) et `.ent-modal-*`.

Structure imposée : `head` (identité à gauche, fermeture à droite, séparateur net) → `body` (la
seule zone qui défile) → `foot` (note de périmètre) ou `actions`. Sections à titres discrets,
`sv-modal-pairs` pour les paires libellé/valeur en deux colonnes, `sv-modal-tiles` pour les tuiles.

⚠️ **Le fond de la carte est neutre** (`--bg-card`, comme toutes les cartes de l'application).
L'accent ne colore que ce qui **porte une information** : le bandeau de message et le badge de
statut. Un fond teinté fait tout crier à la même intensité.

⚠️ **Le nombre de colonnes des tuiles est déclaré, pas déduit.** `auto-fit` posait 4 tuiles en
3 + 1 orpheline selon la largeur : la grille est équilibrée par construction.

⚠️ **`.imh-modal-meta-*` n'avait aucune règle CSS** — c'est ce qui produisait les « informations
du compte en texte brut ». Un balisage sans style ne se voit pas à la relecture, seulement à
l'écran.

Piège corrigé : un encart masquable (`cfgDetailMessageWrap`) ne doit pas être enveloppé dans une
`.sv-modal-section`, sinon le conteneur laisse un blanc quand l'encart est caché. L'espacement
appartient à l'encart (`.sv-modal-espace`), donc il disparaît avec lui.

**`.sv-stat-*`** — modèle unique des cartes d'indicateurs, appelé par la macro
`components/indicateurs.html` (`carte(label, valeur, contexte, icone, ton)`). Remplace **quatre**
familles : `.dash-stat-*` (4 pages), `.sup-tuile-*`, `.ent-tuile-*`, `.profil-stat-*` (2 pages).
Référence retenue : la page « Entreprises partenaires » — libellé court en capitales, valeur en
grand, ligne de contexte dessous.

⚠️ **La ligne de contexte n'est pas optionnelle** : une carte qui en manque casse l'alignement de
la rangée et laisse croire à un oubli. Les deux `profil.html` en ont donc gagné une.

⚠️ **Aucune couleur ne doit prétendre signifier ce qu'elle ne signifie pas.** Les anciennes
teintes (`icon-blue`, `icon-violet`, `icon-amber`) étaient attribuées **par position et non par
sens** : « Utilisateurs » était violet sans raison. *Une couleur qui ne veut rien dire apprend à
ignorer celles qui veulent dire quelque chose.*

⚠️ **Corrigé le 12/08 — la règle ne veut pas dire « pas de couleur du tout ».** Appliquée au pied
de la lettre (icône grise, halo à zéro), elle a produit des cartes plates et ternes. La nuance :
**une teinte identique sur toutes les cartes n'encode rien** — elle ne distingue aucune carte
d'une autre — donc elle ne peut pas induire en erreur. Le défaut est désormais l'accent de la
marque (`--accent`), et seul le **signalement** (`attention`/`eleve`/`critique`, repris de
`criticite.py`) s'en écarte.

Ce qui distingue « ça signale » de « c'est la marque », et qui est le vrai porteur du sens :
la **valeur** prend la couleur (elle reste en texte plein par défaut) et le halo monte.

⚠️ **Une seule variable pilote toute la carte** : `--sv-teinte`. Surface, filet supérieur, halo,
icône, bordure et survol en dérivent, donc une teinte ne peut pas être appliquée à moitié. Un
modificateur de ton ne fait que redéfinir cette variable.

⚠️ **Bug trouvé à cette occasion : `var(--text-light)` n'a jamais existé** dans le bloc `:root`
(les tokens sont `--text`, `--text-muted`, `--text-muted-subtle`, `--text-dim`). Les valeurs des
cartes et les titres de modals héritaient donc d'un gris terne — c'est la moitié de l'effet « tout
noir ». 5 occurrences corrigées. **Un token inexistant ne lève rien en CSS**, il ne se voit qu'à
l'écran : un audit `var(--x)` sans définition correspondante fait désormais partie des contrôles.

**Variante compacte** `.sv-stat--compact` pour les indicateurs **internes** à une carte (page
résultats) : même composant, même hiérarchie, taille réduite. La teinte de fiabilité y est
conservée — elle porte une information, contrairement aux teintes décoratives supprimées.

**Hors périmètre, volontairement** : `svPwdOverlay` (`base.html`) reste sur son propre style. C'est
un blocage plein écran à l'ouverture de session, pas un détail consultable.

Vérifié sur les 17 pages des deux dashboards rendues par l'application : `200` partout, **aucune
trace des 8 familles retirées** dans le HTML servi, 7 modals tous pourvus d'un en-tête et d'un
corps, 25 paires alignées, comptes de cartes exacts page par page (3/4/3/3/4/5/4/3) avec les
quatre lignes sur chacune, et le thème clair couvert par le socle.

### Composition des deux dashboards (12/08/2026)

Agencement uniquement : **la palette, la typographie et les composants existants
(`.sv-stat`, `.sv-modal`, classes de tableaux) n'ont pas changé.** L'identité était déjà établie.

**Le rythme vertical est déclaré une fois** — `--pas-bloc` (1,75 rem) et `--pas-section`
(2,75 rem). Chaque page inventait auparavant son espacement (2 rem sur l'intro, 2,5 rem sur les
titres, `mb-4` en utilitaire) : une page ne peut pas avoir de hiérarchie lisible si son unité
d'espacement change d'un bloc à l'autre.

⚠️ **`.dash-intro-card` supprimée des 16 pages** (hors profil). Elle portait une bordure d'accent
et `box-shadow: var(--shadow-glow)` sur une phrase statique : **le traitement le plus fort de la
page allait à son élément le moins important**, et écrasait la première rangée de cartes. Remplacée
par `.sv-page` — surtitre, titre, guide, emplacement d'action — séparée du contenu par un simple
filet. L'accent est rendu au contenu.

**Le surtitre dit de quel poste on parle** (« Console d'administration » / « Espace entreprise »).
Sur deux consoles qui partagent la même coque, savoir où l'on se trouve est la première information
utile. Il est fixé par le rôle, jamais par la page.

⚠️ **Exception assumée : les deux `profil.html`.** Leur bannière portrait *est* leur en-tête, et son
badge nomme le rôle mieux qu'un surtitre. Leur imposer `.sv-page` aurait produit deux en-têtes.

#### Deux signatures, parce que les deux postes ne font pas le même travail

| | **Entreprise** — elle avance | **Admin** — il arbitre |
|---|---|---|
| Question | « où j'en suis, quoi ensuite ? » | « qu'attend-on de moi ? » |
| Signature | **fil du pipeline** (`.sv-fil`) | **rail de triage** (`.sup-carte`, déjà en place) |
| Ordre | pipeline → prochaine action → activité → résultats/alertes | à traiter → volumes du parc → activité |

**Le fil du pipeline n'est pas un ornement** : c'est la chaîne du modèle de données
(`SourceDonnee` → `ImportDonnee` → `ConfigurationAnalyse` → `ResultatAnalyse` → `Alerte`), où
chaque maillon exige le précédent. C'est ce qui autorise un rang numéroté — il porte une
information réelle, contrairement à un « 01 / 02 / 03 » décoratif.

⚠️ **Un seul maillon est marqué « À faire » : le premier vide.** Au-delà, tout est vide *par
conséquence*, pas par oubli — le signaler partout serait une fausse alerte. `etat_pipeline`
(`app/services/tableau_bord.py`) en déduit **la prochaine action utile**, énoncée une fois en
`.sv-suite`. Un tableau de bord qui constate sans dire quoi faire laisse dans l'impasse.

**La page d'accueil entreprise était vide** — un titre et une phrase, sur la console du rôle qui
*travaille*. Elle porte désormais le fil, la prochaine action, quatre indicateurs et les derniers
résultats/alertes en deux colonnes inégales (les résultats portent le contenu, les alertes sont un
appoint : les mettre à égalité laisserait croire qu'on les consulte autant).

⚠️ **Aucun décompte n'est recalculé** : `activite_par_entreprise` gagne un paramètre
`id_entreprise` et reste le **producteur unique** de ces volumes. Les deux consoles lisent les mêmes
chiffres par les mêmes jointures ; `tableau_bord.py` n'ajoute que l'ordre des étapes et le maillon
bloquant (2 requêtes de plus, pour les imports et les configurations).

**Le périmètre admin est revérifié** : aucune des 8 pages admin n'expose motif, interprétation ni
valeur d'analyse.

Vérifié : `200` sur les 18 pages, 16 portant le nouvel en-tête et les 2 profils leur bannière, un
seul `<h1>` par page, aucun surtitre croisé entre les deux consoles, le fil absent côté admin, et
les 5 états du pipeline (neuve → complète) contrôlés un par un.

### Graphiques des deux accueils (12/08/2026)

`app/services/graphiques.py` (séries et répartitions) + `app/static/js/graphiques.js`
(dessin SVG, ~11 ko). **Aucune dépendance, aucune table, aucune colonne ajoutée** : chaque série
est un `GROUP BY` sur une date déjà au modèle.

**Trois formes, trois métiers** : `sparkline` (tendance 30 j en pied de carte), `courbe` (une
série à la fois, sélecteur + curseur + infobulle), `barres` (répartition).

⚠️ **Une carte ne reçoit de courbe que si sa valeur a une HISTOIRE.** « Dernière activité » est
une **date**, pas une série : lui dessiner une tendance serait en inventer une. Elle reste sur la
carte simple — c'est le seul indicateur demandé qui n'était pas traçable.

⚠️ **L'encours d'alertes est un ÉTAT, pas un flux.** « Non traitée le jour J » vaut `créée ≤ J` et
(`jamais traitée` ou `traitée après J`). Compter les créations donnerait une courbe qui ne
redescend jamais, alors que l'encours redescend. Reconstituable sans historique dédié **parce que
`Alerte.date_traitement` existe** — sans cette colonne, l'indicateur aurait été abandonné.

⚠️ **Les jours sans événement valent 0, ils ne sont jamais omis** : un trou dans une série
temporelle se lit comme une rupture alors qu'il ne dit que « rien ce jour-là ».

**Une seule série est visible à la fois** dans les graphiques à sélecteur. C'est ce qui autorise
une couleur unique (l'accent) : superposer deux séries aurait exigé deux teintes distinguables,
que le design system ne fournit pas pour cet usage.

#### ⚠️ Barres et non anneau — décision mesurée, pas esthétique

L'anneau demandé a été remplacé par des barres après avoir **passé la palette au validateur**
(`dataviz/scripts/validate_palette.js`, mode sombre) plutôt que de l'estimer à l'œil :

| Paire | ΔE vision normale | ΔE deutéranopie | Verdict |
|---|---|---|---|
| `#7F77DD` (classement) ↔ `#378ADD` (prévision) | **8,5** | **0,3** | échec — quasi identiques |
| `#64748b` (besoin libre) ↔ `#7F77DD` | **13,4** | 12,3 | échec — sous le plancher de 15 |
| `#fb923c` (élevé) ↔ `#f87171` (critique) | **10,6** | 7,9 | échec |

Les couleurs d'objectif sont **imposées par le design system** et ne peuvent pas être re-échelonnées.
Or dans un anneau, la couleur est le **seul** porteur d'identité. En barres, chaque part porte son
libellé et son pourcentage : la couleur devient un renfort, plus un porteur — et un lecteur
deutéranope lit la répartition sans effort. Sur les données réelles, les deux plus grosses parts
sont précisément `#7F77DD` (44 %) et `#64748b` (33 %), la paire qui échoue : l'anneau aurait été
illisible là où ça compte le plus.

**Si l'anneau est malgré tout souhaité**, il faudra soit rouvrir les couleurs d'objectif, soit
accepter que deux catégories sur quatre ne se distinguent pas.

#### Couleurs de criticité — désormais déclarées en Python

`COULEUR_PAR_NIVEAU` dans `criticite.py`, **valeurs reprises du CSS, aucune créée**. Elles ne
vivaient que dans la feuille de style (`.res-crit-point--*`) : un graphique qui devait les reprendre
côté serveur n'avait d'autre choix que de les recopier, et de diverger au premier changement.

#### Ce qui n'était PAS calculable, et qui n'a pas été affiché

| Demandé | Pourquoi non |
|---|---|
| Courbe sur « Dernière activité » | c'est une date, pas une série |
| Variation en % vs période précédente | pas de période de comparaison — l'historique couvre 14 jours |
| Dernière connexion | aucun champ sur `Utilisateur` (déjà constaté le 12/08) |

**Piège corrigé** : `| tojson | forceescape` transformait chaque `"` en `&#34;` — six caractères
pour un — soit une charge sextuplée sur 90 points de série. `tojson` seul échappe déjà l'apostrophe
en `'` et suffit dans un attribut entre apostrophes.

Vérifié en trois passes : les séries sur données réelles (30 points pleins, zéros compris), le
rendu servi (couleurs uniquement issues des objectifs et de la criticité, aucun delta, périmètre
admin intact), et **la géométrie des tracés rejouée hors navigateur** — tous les points dans le
cadre, aucun sommet collé au plafond, aucune barre hors de [3, 100] %.

### Densité des deux accueils — la collision `.sv-section` (12/08/2026)

Symptôme : les accueils s'étalaient sur **trois écrans**, avec des vides de plusieurs centaines de
pixels entre les rangées, et des colonnes de hauteurs très inégales.

⚠️ **Cause principale : une collision de noms de classe.** `.sv-section` existait déjà pour le
**site vitrine**, avec `padding: 5.5rem 0` — soit **176 px morts entre chaque rangée**. Le socle de
composition du dashboard avait repris le même nom en n'ajoutant qu'un `margin-top` : le `--pas-section`
n'a jamais été en charge de quoi que ce soit. Renommé en **`.dash-rangee`** (+ `-tete`, `-titre`,
`-note`, `-lien`) ; le rythme du site vitrine est intact.

**Le préfixe `sv-` est partagé avec le site vitrine.** Avant d'y créer une classe, vérifier qu'elle
n'existe pas déjà en amont de la feuille — une redéfinition partielle ne lève rien, elle se voit
seulement à l'écran. Les 22 classes `.sv-*` du site vitrine sont listables par un balayage des
définitions antérieures au socle du dashboard.

Trois autres corrections, chacune sur une cause identifiée :

| Grief | Cause | Correction |
|---|---|---|
| Colonnes de hauteurs inégales | `.sv-colonnes { align-items: start }` | `stretch` + colonne en pile + `.sv-panneau { flex: 1 }` |
| Le rail occupe toute la largeur pour une entrée | `repeat(auto-fit, …)` étire la carte unique | `auto-fill` : elle garde sa largeur de colonne |
| En-tête = la moitié du premier écran | surtitre + séparateur + titre à 1,65 rem | surtitre retiré du rendu, séparateur supprimé, titre à 1,4 rem |

⚠️ **Le surtitre n'est plus rendu.** Il répétait ce que la barre latérale dit déjà. Le paramètre
reste dans la macro `entete()` — les appels existants restent valides et documentent le poste.

**Contenus qui remplissent leur cadre** : `.sv-parts` et `.sv-classement` passent en
`height: 100%; justify-content: center`. Deux lignes dans un panneau étiré s'entassaient en haut ;
elles se centrent désormais dans la hauteur que la rangée leur donne.

**Gouttière unique** : `--pas-section` (1,35 rem) sépare les rangées **et** les colonnes d'une
rangée ; `--pas-bloc` (0,9 rem) sépare les cartes d'une même grille. Deux valeurs, pas plus.

Mesuré après correction, en sommant les hauteurs déclarées : **~849 px pour l'accueil admin**
(en-tête 70 · indicateurs 124 · rail 99 · graphique 278 · listes 278), contre ~1 640 px pour deux
écrans utiles. L'accueil entreprise ajoute le fil du pipeline (~110 px).

Vérifié par six suites : composition, densité (gouttière, hauteurs, en-tête, rail, total),
graphiques, harmonisation, cartes, page entreprises.

### Icône d'objectif : une définition, plus cinq (12/08/2026)

`app/templates/components/objectifs.html` — macros `obj_icon(icon, size)` et
`obj_pastille(icon, color, size)`.

⚠️ Le jeu d'icônes vivait en **cinq copies** (liste des configurations, wizard, alertes, résultats,
et une pastille générique sur l'accueil), avec pour seule garantie une note de ce fichier demandant
de les tenir identiques. **Elles avaient déjà divergé** : la liste rendait encore l'ancienne icône
« adjustments » là où le wizard affichait `arrows-sort`. *Une note ne remplace pas une source
unique.* Les cinq gabarits consomment désormais le composant ; il ne reste aucune macro locale.

L'icône vient de `_presenter_objectif(...)["icon"]`, comme la couleur et le libellé : le nom, la
teinte et le pictogramme d'un objectif ont la même origine.

### Le badge de criticité est un composant, pas un jeton générique (12/08/2026)

L'accueil entreprise affichait la criticité avec `.dash-status-pending` — **le jeton qui affiche
aussi « En attente » ailleurs dans l'application**. Le libellé était pourtant le bon (« À
surveiller », issu de `LIBELLE_PAR_NIVEAU`) : c'est le *vêtement* qui faisait lire un niveau de
criticité comme un statut de traitement.

Remplacé par `.res-crit` / `.res-crit-point`, le badge de la page des résultats. Le libellé et la
teinte viennent tous deux de `criticite.py`, donc **l'accueil et la page des résultats ne peuvent
plus diverger**. Vérifié niveau par niveau sur les deux pages.

⚠️ **`attention` est un code, « À surveiller » est le libellé.** Le code apparaît en base, dans les
noms de classes CSS et dans les paramètres d'URL ; le libellé est ce qu'on montre. Afficher
`attention` dans un badge reviendrait à exposer un identifiant interne.

**Les listes remplissent leur panneau** : `.sv-liste` passe en `height: 100%; justify-content:
center`, et `.sv-liste-vide` en `flex: 1`. Deux entrées dans un bloc étiré par sa rangée
laissaient un vide en bas — même correction que `.sv-parts` et `.sv-classement`.

### Section admin « Analyses » (12/08/2026)

`app/services/supervision_analyses.py` + `admin_configurations.html`. Remplace le placeholder.

⚠️ **Route déclarée AVANT `/dashboard/admin/{section}`** : le catch-all l'absorberait sinon.

#### ⚠️ Fuite de périmètre trouvée dans une fonction partagée

`_presenter_objectif` — la source unique d'affichage d'un objectif, utilisée des deux côtés —
**expose le besoin métier** : elle le place dans `sous_ligne` **et** dans `titre` (l'infobulle),
y compris pour une configuration qui porte pourtant un objectif prédéfini, et le renvoie comme
`label` pour les configurations « besoin libre ». L'employer côté admin aurait publié le besoin
de chaque entreprise sur une page de supervision.

**`objectif_pour_admin(c)`** est la frontière : elle **énumère ce qui sort** (`libelle`, `icone`,
`couleur`, `libre`) au lieu d'exclure ce qui ne doit pas sortir — même principe que `charge_utile()`
en 4.5. Le jour où `_presenter_objectif` gagne un champ, il ne franchira pas la frontière tout seul.
Pour un besoin libre, la **mention** `MENTION_BESOIN_LIBRE` remplace le texte.

Audit complet des autres fonctions partagées : `activite_par_entreprise`, `entreprises_actives`,
`synthese_admin`, `etat_pipeline` ne renvoient que des volumes ; `alertes.py` lit `criticite_motif`
mais uniquement pour le côté entreprise. **Aucune autre fuite.** Vérifié en cherchant les 45 valeurs
sensibles réellement présentes en base dans les 9 pages admin : aucune n'apparaît.

#### Ce qui n'est pas calculable, et n'a pas été inventé

⚠️ **Il n'existe aucun historique des tentatives.** `execution_erreur`, `intention_erreur` et
`message_execution` ne gardent que la **dernière**. « Échecs répétés » exigerait une table
d'exécutions. Trois signaux le remplacent, tous calculables :

| Signal | Calcul |
|---|---|
| `jamais_lancee` | active, `derniere_execution IS NULL` |
| `sans_resultat` | lancée, mais aucun `ResultatAnalyse` — 2 cas réels (cfg 68, 75) |
| `en_echec` | statut d'erreur ou colonne d'erreur renseignée |

Un seul signal s'affiche par ligne, le plus actionnable : en afficher trois noierait le message.

⚠️ **Le modal le dit lui-même** : l'historique ne liste que les exécutions **ayant produit un
résultat**, car un échec ne crée aucune ligne. Un journal partiel présenté comme complet ferait
croire qu'une analyse n'a jamais échoué.

**`nature_erreur()` classe un échec sans recopier son message** : seule la *colonne* qui porte
l'erreur est lue, ce qui suffit à donner la nature (source indisponible / service d'interprétation /
moteur de calcul) et l'action à mener. Le message d'exécution est rédigé pour l'entreprise et peut
nommer ses colonnes — il ne franchit pas la frontière.

#### Contrôle de saisie

Tout paramètre d'URL est confronté aux valeurs connues **avant** d'atteindre la couche de données ;
`entreprise_id` n'est converti que s'il désigne une entreprise existante. Une valeur inconnue
retombe sur le défaut et **liste tout** — vider silencieusement la page serait pire que l'ignorer.
Éprouvé sur 6 saisies invalides, dont une tentative d'injection : 200 partout, liste complète.

**Une requête pour tout le parc** : le dernier résultat de chaque configuration se prend en
parcourant une liste triée, pas par un `GROUP_CONCAT ... ORDER BY` propre à MySQL.

Vérifié : 9 configurations listées, les 9 filtres de statut et de fréquence confrontés un par un à
un décompte SQL indépendant, aucune fuite sur 4 combinaisons de filtres, un seul type de bouton
dans le tableau, et une entreprise qui demande l'URL est redirigée (303).

### Centre d'alertes admin : des volumes aux décisions (12/08/2026)

**Retiré : le bloc « À surveiller ».** Il occupait un tiers de largeur pour cinq lignes serrées et
**répétait les cartes situées juste dessous** — « 1 alerte critique non traitée » était déjà dit
par les cartes « Critique » et « Non traitées ». Une information dite deux fois n'est pas dite
plus fort.

**Cinq cartes ramenées à quatre.** « Alertes au total » a sauté : un cumul incluant les alertes
déjà traitées ne dit rien de l'état du parc. Il est remplacé par **« Plus ancienne ouverte »**,
qui distingue « des alertes arrivent » de « des alertes s'accumulent ».

#### Ce qui manquait pour décider

| Ajout | Calcul — `date_creation` et `date_traitement` suffisent |
|---|---|
| Ancienneté du plus vieux dossier ouvert | `min(date_creation)` sur les non traitées, par entreprise |
| Délai moyen de traitement | `avg(date_traitement − date_creation)` sur les traitées |
| Évolution de l'encours | `alertes_ouvertes_par_jour` — le composant du tableau de bord |

⚠️ **L'encours est un ÉTAT, pas un flux** : il redescend quand les entreprises traitent, là où un
cumul de créations ne redescend jamais. Vérifié : la dernière valeur de la courbe égale le nombre
d'alertes non traitées en base.

⚠️ **« Aucune alerte encore traitée » s'affiche toujours.** Ce repli était d'abord calculé mais
jamais rendu — or c'est **le cas le plus parlant** : il distingue une entreprise réactive d'une
entreprise qui laisse tout ouvert. Aucune moyenne n'est inventée quand rien n'a été traité.

**Tri par ce qui appelle l'attention**, jamais alphabétique : critiques ouvertes, puis ancienneté
du plus vieux dossier, puis encours. Le nom ne départage que deux situations identiques.

`SEUIL_ALERTE_ANCIENNE = 14` jours : au-delà, une alerte non traitée n'est plus un événement
récent. Le seuil **qualifie** l'ancienneté, il ne déclenche rien.

**Périmètre inchangé** : ni message, ni motif, ni lien vers un résultat. Vérifié en cherchant les
10 valeurs sensibles réellement en base — aucune n'apparaît. L'ancienneté est confrontée à un
décompte SQL indépendant.

### Aperçu d'un import — côté entreprise uniquement (12/08/2026)

`apercu_import(db, imp, source)` dans `extraction.py` + endpoint
`GET /dashboard/entreprise/imports/{source_id}/apercu`.

**La restriction précédente n'avait pas lieu d'être** : « le contenu brut n'est jamais affiché ici »
s'appliquait aux données **de l'entreprise elle-même**. Le modal montre désormais les 10 premières
lignes, avec en-têtes et compte total — un contrôle avant configuration (colonnes alignées, dates
bien interprétées), pas un explorateur : ni pagination, ni recherche.

⚠️ **Le fichier n'est jamais chargé en entier.**

| Format | Lecture |
|---|---|
| CSV | `read_csv(nrows=10)` pour l'aperçu, `chunksize=50_000` pour le compte exact |
| SQL | lecture bornée à `OCTETS_APERCU_SQL` (512 Ko), assez pour le premier `CREATE` et ses `INSERT` |

Les deux lecteurs sont **ceux du profilage du wizard**, et le typage passe par `_normaliser_types` :
ce que l'aperçu montre est ce que l'analyse lira. Mesuré sur un CSV de 9,7 Mo / 300 000 lignes
fabriqué pour l'épreuve : **15 ms** pour les 10 lignes, 0,11 s pour le comptage par blocs.

⚠️ **Sur un script SQL, le total est celui du fragment lu**, pas du fichier. `total_partiel` le dit
au gabarit, qui écrit « sur au moins N » — annoncer un total qu'on n'a pas compté serait faux.

⚠️ **L'appartenance est vérifiée dans la REQUÊTE**, pas après : le filtre sur `idEntreprise` fait
partie du `WHERE`. Une source d'autrui est *introuvable* (404), elle n'est pas « trouvée puis
refusée ». Éprouvé sur 4 identifiants hors périmètre, plus un non numérique (422).

⚠️ **Le modal administrateur est inchangé** : aucun aperçu, et sa note de périmètre reste. Vérifié
par un test dédié — l'endpoint lui-même le redirige (303).

**L'aperçu est chargé à l'ouverture du modal**, jamais avec la page : dix sources listées
déclencheraient sinon dix lectures de fichier pour rien. Vérifié : aucune valeur du fichier
n'apparaît dans le HTML servi.

### Couleurs

Reprises de `criticite.py`, aucune créée : `eleve` → `#fb923c` (orange), `critique` → `#f87171`
(rouge, réservé à ce seul niveau). Le liseré porte l'intensité — « élevé » ne crie pas aussi
fort que « critique ». **Le vert n'apparaît nulle part** : il reste réservé aux confirmations de
succès. Les rangs 0 et 1 ne créant pas d'alerte, seules deux teintes existent ici.

### Vérification

`test_alertes.py` — 26 contrôles sur une base **SQLite en mémoire** construite depuis les vrais
modèles : mêmes contraintes qu'en production, sans toucher à MySQL. Couvre le seuil, la création,
l'idempotence, la mise à jour sur changement de rang, le retrait sous le seuil, **plusieurs
alertes pour une même configuration** (ce que l'ancienne unicité interdisait), le cloisonnement
entre entreprises et l'absence de détail métier côté admin.

`generer_alertes_existantes.py` rejoue la même fonction sur les résultats déjà en base — donc
crée, met à jour et retire aussi. Relancer après un changement de seuils réaligne tout le parc.

## Données de démonstration — à retirer avant la livraison

### Jeu « DEMO-ALERTES » (10/08/2026) — Module 5

`demo_alertes.py` crée deux analyses qui déclenchent chacune un niveau, sur deux types
d'analyse différents. **Rien n'est inséré à la main dans `ResultatAnalyse` ni dans `Alerte`** :
le script fait tourner la vraie chaîne 4.1 → 4.2 → 4.4 → Module 5, donc ce qui est montré est
ce que produit l'application.

| Analyse | Données | Criticité obtenue |
|---|---|---|
| Classement (`comparaison_classement`) | 5 produits, « Console Nova » à **78 %** du total | **élevé** (rang 2) |
| Anomalie (`detection_anomalie`) | 30 relevés dont 4 à ×3,2 → **13,3 %** | **critique** (rang 3) |

Valeurs **calibrées contre `criticite.py`**, pas devinées : seuil de concentration à 70 % pour
« élevé », taux d'anomalies à 10 % pour « critique ». Le script vérifie le niveau obtenu et
signale tout écart. Graine aléatoire fixe : la démonstration donne le même résultat à chaque
exécution.

⚠️ La spécification passe par `specification_par_defaut` (repli déterministe) : **aucun appel
LLM, aucun quota consommé**, et un résultat reproductible.

**Traçabilité et retrait.** Tout porte le marqueur `DEMO-ALERTES` — nom de source, fichiers CSV
sous `app/uploads/`. Retrait intégral :

```
python demo_alertes.py --supprimer
```

La cascade emporte imports, configurations, résultats et alertes ; les deux CSV sont supprimés.
`python demo_alertes.py` sans argument affiche ce qui serait créé et ce qui serait supprimé,
sans rien écrire.

Créé le 10/08/2026 sur l'entreprise 7 : sources 27-28, imports 33-34, configurations 78-79,
résultats 30-31, 2 alertes.

⚠️ **Défaut trouvé grâce à ce jeu** : `synthese_admin` lisait `Entreprise.nom_entreprise`, champ
inexistant — le vrai nom est `Entreprise.nom`. La page admin renvoyait 200 tant qu'aucune alerte
n'existait (`synthese_admin` sortait avant la lecture des noms) et serait passée à 500 dès la
première. **Un test sur une table vide ne teste pas le chemin nominal.**

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
  - tâche 4.3 (traduction de l'intention) : terminée le 06/08/2026 — vocabulaire fermé,
    schéma Pydantic, repli déterministe sans clé API
  - tâche 4.4 (exécution, moteur de calcul) : terminée le 06/08/2026 — 4 opérations, 3 modèles
    de prévision selon la fiabilité. Consomme `ResultatPreparation` sans réécriture
    (`.table.donnees` est déjà indexé par le temps, trié, agrégé)
  - tâche 4.5 (restitution en langage naturel) : terminée le 07/08/2026 — liste blanche de
    la charge utile, format de réponse fermé, plafond de confiance vérifié côté serveur,
    indisponibilité du service sans perte des chiffres
  - tâche 4.6 (indicateurs de suivi et criticité) : terminée le 08/08/2026 — bloc statistique
    commun aux quatre opérations, `erreur_ajustement` distinct de `variation_pct`, criticité
    déterministe à 4 niveaux avec justification stockée. Base de déclenchement du Module 5.
    Reste leur exposition sur la page de résultats
  - tâche 4.7 (stockage et restitution) : terminée le 10/08/2026 — `ResultatAnalyse` porte
    les chiffres, les séries, la criticité et l'interprétation ; la page
    « Résultats & prévisions » les rend visibles. **Module 4 complet.**
- Module 5 (alertes) : terminé le 10/08/2026 — déclenchement sur `criticite_rang >= 2`,
  cloche et panneau du topbar, page « Mes alertes » avec marquage traité, vue de supervision
  admin. **Correction du modèle : la relation devient Résultat → Alerte** (voir la section
  dédiée)
- Module 6 (Power BI) : non commencé

## Journal des sessions

### 10/08/2026 — Page de résultats, dernière pièce du Module 4

Fait : route `/dashboard/entreprise/resultats` (déclarée avant le catch-all `{section}`),
`resultats.html`, ~250 lignes de CSS, `_presenter_objectif` remontée au niveau module,
`date_modification` + sa migration.

Validé : template rendu hors requête sur 4 cartes couvrant tous les cas particuliers —
prévision non concluante sans interprétation, classement conclusif avec changement de tête,
anomalie avec plafonnement de criticité, besoin libre avec évolution bloquée — plus les deux
états vides. 19 contrôles.

Vérifié ensuite sur les données réelles, migration passée : 13 cartes, `HTTP 200` sur la page
et ses deux filtres, CSS servi, JavaScript syntaxiquement valide (`node --check`), et **13/13
graphiques disposant d'une série exploitable** (bande d'intervalle comprise sur les 6 prévisions).

Reste : rien pour le Module 4. La suite est le Module 5 (alertes), qui consommera
`criticite_rang` sans retraitement.

⚠️ **`date_modification` est NULL sur toutes les configurations existantes** : la colonne vient
d'être créée et `/finalize` n'a pas encore été rejoué. Le garde-fou « configuration modifiée »
ne protège donc que les exécutions à venir — les lignées historiques affichent leur delta sans
qu'on puisse savoir si un paramétrage a changé entre-temps. C'est le cas de cfg 59
(41,7 → 51,8 → 41,7 %). Rien à corriger : l'information n'existe pas rétroactivement.

### 08/08/2026 — Tâche 4.6 (section dédiée plus haut)

Ordre de travail imposé par le brief et suivi : **recenser, proposer les règles, attendre
validation, implémenter**. Le recensement a évité de redéfinir `moyenne`/`ecart_type`, qui
existaient déjà côté anomalie — les clés ont été reprises, pas doublées.

Trois arbitrages validés avant écriture : seuils génériques mais centralisés (réglables par
configuration sera un **ajout**, pas une refonte), classement plafonné à `eleve`, quatre niveaux
conservés pour que le Module 5 filtre à sa convenance sur le rang numérique.

Défaut latent trouvé en chemin : la clé `ecart_type` des observations aberrantes masquait
l'indicateur global du même nom, et `charge_utile` lisait `ecart_zscore` — l'ampleur des
anomalies ne parvenait jamais à l'interprétation. Renommée.

### 08/08/2026 — Bascule sur Gemini (section « Fournisseur de modèle de langage »)

Fait :
- `llm_client.py` : point d'accès unique, registre `FOURNISSEURS` (gemini, anthropic),
  adaptation du schéma par fournisseur, `LLMIndisponible` comme erreur commune.
- `traduction.py` et `interpretation.py` vidés de tout ce qui touchait à l'API ; leur
  `_appeler_llm` ne fait plus qu'appeler et retraduire l'erreur.
- `nettoyer_schema()` déplacé dans `llm_client` (c'est l'adaptateur Anthropic) :
  `schema_json()` et `schema_reponse()` rendent le schéma Pydantic brut.
- `config.py` : `LLM_PROVIDER` ajouté, défauts basculés sur Gemini, `LLM_BASE_URL` facultatif.

Validé par appels réels : traduction sur la source CSV 23 (7 formulations), interprétation sur
cfg 58 (prévision non conclusive → confiance « faible », texte qui refuse de trancher) et
cfg 59 (classement → confiance « élevée »). Repli sans clé revérifié sur les deux couches.

À savoir pour la suite : la constatation que Gemini n'applique pas `maxLength` est arrivée par
un échec de validation, pas par la documentation. **Tester la contrainte plutôt que la lire**
— l'API accepte des mots-clés qu'elle n'honore pas.

Complété le même jour (section « Longueurs : marge de sécurité et seconde tentative ») : marge
de 25 % sur les longueurs annoncées, détail de rejet exploitable, journalisation du contenu
rejeté, une reprise ciblée. Modèle par défaut passé à `gemini-3.5-flash` — `gemini-2.5-flash`
est plafonné à 20 requêtes/jour sur le palier gratuit.

### 08/08/2026 — Guidage : colonnes non mesurables et tables vides (section dédiée plus haut)

Fait :
- `nom_evoque_identifiant()`, `valeurs_forment_une_cle()`, `colonnes_identifiantes()` dans
  `extraction.py` ; `_cles_primaires_sql()` + `_corps_create_table()` extrait de
  `_colonnes_create_table()` (le corps du CREATE est parsé une fois, exploité trois fois).
- `colonne_est_booleenne()`, `booleens_declares_bdd()`, `booleens_declares_sql()`,
  `colonnes_booleennes()` : un indicateur oui/non est une dimension, jamais une mesure.
  `specification_par_defaut` sait le retenir comme dimension à défaut de colonne texte.
- `est_mesure` sur chaque colonne du profil ; consommé par `_evaluer_objectifs`,
  `valider_colonnes`, `specification_par_defaut` et le prompt de traduction.
- `resume.tables_vides`, `resume.colonnes_identifiants`, `resume.colonnes_booleennes` ;
  `_CLES_SANS_MESURE` (4 constats selon ce qui est écarté) ; deux nouvelles branches en tête
  de `majGuideSelection()`.

- Simplification du bandeau (section « Le bandeau tient en une phrase ») : `points` retiré de
  `majGuide()`, `<ul class="wiz-guide-list">` et son CSS supprimés, détail du profil déplacé
  dans les logs ; note explicative quand la fréquence n'influence pas le résultat.

Validé sur la base réelle (11 tables) — voir la section dédiée. Template rendu hors requête
pour vérifier qu'il ne subsiste aucune énumération de colonnes.

Deux pièges rencontrés pendant la session, corrigés :
- le motif de colonne du CREATE TABLE matche aussi `PRIMARY KEY (...)` : sans filtre sur les
  mots-clés de contrainte, « PRIMARY » entrait dans les clés primaires. Les deux lectures du
  corps partagent désormais `_MOTS_CLES_CONTRAINTE` ;
- « unique et croissant » seul écartait une colonne de montants triés — la condition d'entier
  a été ajoutée après l'avoir constaté en test, pas déduite a priori ;
- la détection oui/non par le contenu seul ratait `doit_changer_mdp` : `utilisateur` ne compte
  que 2 lignes, sous le seuil de 5 valeurs. C'est le constat qui a motivé le critère de
  schéma — **quand un schéma existe, il tranche mieux qu'un échantillon.**

### 07/08/2026 — Module 4, tâche 4.5 (restitution en langage naturel)

Fait :
- `interpretation.py` (schéma fermé, charge utile en liste blanche, plafond de confiance,
  point d'entrée qui ne lève jamais) + branchement en étape 8 de `/lancer`.
- `nettoyer_schema()` extrait de `schema_json()` : la contrainte de l'API sur les schémas de
  sortie structurée est connue à **un seul endroit**, plutôt que redécouverte par un échec
  au premier appel de 4.5.
- 4 colonnes sur `ResultatAnalyse` + `migrate_resultat_interpretation.py` (exécutée).
- `ResultatExecution.id_resultat`, renseigné par `executer_et_stocker`.
- `test_interpretation.py` : `--frontiere`, `--plafond`, `--indispo`, `--tout`, `<id>`,
  `<id> --commit`.
- Renumérotation des tâches dans ce fichier (4.3 traduction, 4.4 exécution) — l'ajout de la
  couche de traduction avait décalé la numérotation sans que les sections suivent.

Validé : 3/3 séries de test sans clé API, plus la chaîne complète en base sur cfg 58 et 59.

Piège corrigé pendant la session : le mode `--commit` du test appelait `executer_calcul`
seul, donc sans ligne `ResultatAnalyse` — `id_resultat` restait `None` et 4.5 n'écrivait
rien tout en affichant un succès. Un test d'écriture doit passer par le **chemin réel du
lancement** (`executer_et_stocker`), sinon il valide un scénario qui n'existe pas.

Également fait ce jour — **guidage en langage métier** (section dédiée plus haut) :
`adequation_frequence()` dans `fiabilite.py`, `_BLOCAGES` dans `dashboard.py`, refonte de
`majFrequences()` / `majFreqNote()` / `appliquerCompatibilite()`, styles `.wiz-obj-fix` et
`.wiz-freq-note--*`. Vérifié sur un profil de 30 lignes réparties sur 6 mois : quotidien et
ponctuel silencieux, hebdomadaire « peu précis », mensuel « non réalisable ».

Reste :
- **La page de résultats (4.6/4.7) est le seul vrai trou** : tout ce que le moteur produit
  est en base et n'est affiché nulle part. `resultat_json` porte déjà les séries de
  visualisation, `interpretation_json` la rédaction structurée.
- `intention_reformulee` doit s'afficher **avec le résultat** (2ᵉ des deux emplacements du
  garde-fou ; le 1er, le modal de détail, est fait).
- Sans `LLM_API_KEY`, 4.5 est démontrable dans son comportement dégradé uniquement. La
  rédaction elle-même n'a pas encore été observée sur un appel réel.

### 06/08/2026 — Module 4 : couche de traduction de l'intention

Fait :
- `schema_analyse.py` + `traduction.py` ; config LLM (3 variables) ; renommage `besoin` +
  `specification_json` + `intention_reformulee` avec `migrate_configuration_analyse_besoin.py`.
- `test_traduction.py` : 7 formulations d'exemple, `--repli` pour forcer le déterministe.

Validé :
- 4 opérations couvertes par le repli, sans aucune clé API.
- 6 rejets sur 6 tentatives non conformes (opération inconnue, colonne inventée, mauvais type
  de colonne, clé injectée `executer_sql`, reformulation absente, borne dépassée).
- Interruption explicite quand ni LLM ni objectif ne sont disponibles.

Limite connue du repli (attendue, pas un défaut) : il ignore la nuance de formulation.
« quels articles mettre en promotion » devrait donner `ordre: croissant` ; le repli rend
`decroissant` comme pour « les plus vendus ». C'est précisément la valeur ajoutée du LLM.

Reste pour la prochaine étape :
- UI Module 3 : libellé « Que souhaitez-vous savoir précisément ? », exemples sous le champ,
  validation « objectif ou besoin obligatoire » à la finalisation. Non fait volontairement —
  l'ordre de travail demandait la couche de traduction *seule*.
- Affichage de `intention_reformulee` dans le modal de détail (garde-fou, 1er des 2 emplacements).
- Tâche 4.4 (exécution), dans un prompt séparé.

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
