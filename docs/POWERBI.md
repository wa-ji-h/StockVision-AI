# Module 6 — Exploitation Power BI Desktop

StockVision AI expose ses résultats d'analyse à Power BI Desktop par **7 vues SQL en
lecture seule**. Power BI se connecte directement à la base MariaDB ; il ne passe pas
par l'application.

---

## 1. Créer les vues

```bash
venv\Scripts\activate
python migrate_vues_powerbi.py              # crée ou remplace les 7 vues
python migrate_vues_powerbi.py --verifier    # contrôle vues et droits, n'écrit rien
python migrate_vues_powerbi.py --supprimer   # retire les vues
```

Le script est **idempotent** (`CREATE OR REPLACE VIEW`) : le relancer après un
changement de modèle met les vues à jour sans rien casser. **Aucune table n'est
modifiée ni créée.**

---

## 2. Créer le compte Power BI

### 2.1 Commandes

À exécuter avec un compte administrateur MariaDB, **après** la création des vues :

```sql
-- 1. Le compte. Choisir un mot de passe fort, propre à cet usage.
CREATE USER 'powerbi'@'%' IDENTIFIED BY '<mot-de-passe-fort>';

-- 2. SELECT sur les 7 vues, une par une. Jamais de GRANT sur la base entière.
GRANT SELECT ON stock_vision.vw_pbi_entreprises TO 'powerbi'@'%';
GRANT SELECT ON stock_vision.vw_pbi_resultats   TO 'powerbi'@'%';
GRANT SELECT ON stock_vision.vw_pbi_series      TO 'powerbi'@'%';
GRANT SELECT ON stock_vision.vw_pbi_classement  TO 'powerbi'@'%';
GRANT SELECT ON stock_vision.vw_pbi_anomalies   TO 'powerbi'@'%';
GRANT SELECT ON stock_vision.vw_pbi_alertes     TO 'powerbi'@'%';
GRANT SELECT ON stock_vision.vw_pbi_activite    TO 'powerbi'@'%';

FLUSH PRIVILEGES;
```

Restreindre l'hôte est préférable à `'%'` dès que l'adresse du poste est connue :

```sql
CREATE USER 'powerbi'@'192.168.1.50' IDENTIFIED BY '<mot-de-passe-fort>';
```

### 2.2 Pourquoi ces droits suffisent

⚠️ **Une vue s'exécute avec les droits de son créateur** (`SQL SECURITY DEFINER`,
le défaut). Le compte `powerbi` lit donc les vues **sans aucun droit sur les tables
sous-jacentes**. C'est ce qui rend l'isolement réel plutôt que déclaratif.

Ce que le compte **ne peut pas** atteindre, faute de `GRANT` :

| Table | Contenu protégé |
|---|---|
| `Utilisateur` | mots de passe hachés, identifiants OAuth |
| `ConnexionBDD` | identifiants de bases tierces chiffrés (`user_chiffre`, `password_chiffre`) |
| `PasswordResetToken` | jetons de réinitialisation |
| `Administrateur` | comptes d'administration |

**Ne jamais écrire** `GRANT SELECT ON stock_vision.* TO 'powerbi'@'%'` : cette forme
donnerait accès à toutes les tables, y compris aux trois ci-dessus.

### 2.3 Contrôle

```bash
python migrate_vues_powerbi.py --verifier
```

Le script vérifie que chaque vue répond, qu'aucune ne porte de colonne de mot de
passe, de jeton ou de secret, qu'aucune ne reverse de JSON brut, et que le compte
`powerbi` ne détient aucun droit au niveau de la base ni sur une table sensible.

---

## 3. ⚠️ Portée de cet accès — à connaître et à assumer

**Qui détient ces identifiants.** Le compte `powerbi` est un **canal d'exploitation
interne**. Ses identifiants sont détenus par l'éditeur de la plateforme et ne sont
**jamais distribués aux entreprises clientes**.

**Ce que cet accès permet de voir.** Toutes les données d'analyse de **toutes les
entreprises** : valeurs calculées, prévisions, éléments classés — y compris les
libellés métier (noms de produits, de clients) — niveaux de criticité et volumes
d'alertes.

**Il n'est pas cloisonné par entreprise.** Un rapport peut filtrer sur
`id_entreprise`, mais rien ne l'y oblige : le filtre est un confort de lecture, pas
une barrière technique.

**Ce n'est pas une contradiction avec le cloisonnement de l'application, c'est un
niveau de privilège différent.** Le dashboard administrateur masque délibérément le
contenu métier parce qu'un administrateur applicatif n'a pas à le connaître pour
faire son travail — il supervise des volumes et des états. L'exploitation Power BI
répond à un autre besoin, exercé par l'éditeur sur ses propres données de service,
avec des identifiants qui ne quittent pas l'organisation.

**Conséquence pratique :** traiter le mot de passe `powerbi` avec le même soin qu'un
mot de passe d'administration de base, et le faire tourner quand une personne
quitte l'équipe.

### 3.1 Si cet accès devait un jour être ouvert aux entreprises

**Ne pas distribuer le compte `powerbi`.** La voie à suivre est **un compte par
entreprise, sur des vues filtrées** :

```sql
-- Une vue par entreprise, filtrée à la source.
CREATE OR REPLACE VIEW vw_pbi_resultats_e7 AS
    SELECT * FROM vw_pbi_resultats WHERE id_entreprise = 7;

CREATE USER 'pbi_e7'@'%' IDENTIFIED BY '<mot-de-passe>';
GRANT SELECT ON stock_vision.vw_pbi_resultats_e7 TO 'pbi_e7'@'%';
```

Le filtre vit alors **dans la vue**, pas dans le rapport : une entreprise ne peut pas
le retirer. Coût : autant de jeux de vues et de comptes que d'entreprises, à créer et
à révoquer avec le cycle de vie des comptes clients. C'est le prix d'un cloisonnement
réel, et il est justifié dès lors que les identifiants sortent de l'organisation.

---

## 4. Connexion depuis Power BI Desktop

1. **Accueil → Obtenir les données → Base de données → Base de données MySQL**
   (le connecteur MySQL fonctionne avec MariaDB).
2. Renseigner :
   - **Serveur** : `localhost:3306` — ou l'adresse du serveur, port compris
   - **Base de données** : `stock_vision`
3. **Authentification → Base de données** : utilisateur `powerbi` et son mot de passe.
4. **Mode de connectivité : Importer.** Les volumes sont modestes et les vues font
   déjà le travail d'agrégation ; DirectQuery rejouerait l'extraction JSON à chaque
   interaction.
5. Cocher les 7 vues `vw_pbi_*`, puis **Charger**.

> Le connecteur MySQL de Power BI exige **MySQL Connector/NET** sur le poste. Power BI
> Desktop propose son téléchargement si le composant manque.

---

## 5. Dictionnaire des vues

### `vw_pbi_entreprises` — dimension
Une ligne par entreprise. C'est la table de dimension du modèle.

| Colonne | Description |
|---|---|
| `id_entreprise` | clé de jointure présente dans **toutes** les vues |
| `entreprise`, `secteur` | identité |
| `date_inscription`, `statut_demande` | cycle de vie du compte |

### `vw_pbi_resultats` — une ligne par exécution
Le cœur du modèle : 34 colonnes.

| Groupe | Colonnes |
|---|---|
| Contexte | `id_resultat`, `id_entreprise`, `entreprise`, `id_configuration`, `objectif`, `type_analyse`, `date_execution`, `frequence`, `source`, `type_source` |
| Valeurs | `valeur_analysee`, `valeur_prevue`, `intervalle_bas`, `intervalle_haut`, `methode_intervalle`, `unite` |
| Modèle | `modele_applique`, `modele_libelle`, `fiabilite`, `fiabilite_libelle`, `points` |
| Verdict | `criticite`, `criticite_rang`, `criticite_libelle`, `conclusif`, `sens` |
| **Calculés** | `ecart_observe_prevu`, `ecart_pct`, `largeur_intervalle`, `largeur_relative`, `age_execution_jours` |
| Contrôle | `nb_points_historique`, `nb_points_prevus`, `serie_tronquee` |

⚠️ **`conclusif = 0` : ne pas afficher la variation comme un fait.** Le moteur signale
ainsi que l'intervalle englobe la valeur de départ — il ne distingue pas une hausse
d'une baisse. Prévoir un visuel qui le dise, comme le fait l'application.

⚠️ **`largeur_relative` proche de 1 ou au-delà** : l'intervalle est aussi large que la
valeur prévue. L'ampleur existe, elle n'est pas exploitable.

### `vw_pbi_series` — une ligne par point
C'est la vue qui permet de **tracer les courbes**.

| Colonne | Description |
|---|---|
| `id_resultat`, `id_entreprise`, `id_configuration` | rattachement |
| `position` | rang du point dans la série (tri) |
| `date_point`, `valeur` | le point |
| `nature` | **`observe`** ou **`prevu`** — la colonne à mettre en légende |
| `borne_basse`, `borne_haute` | intervalle de confiance, renseigné sur `prevu` uniquement |

Historique et prévision sont réunis dans une seule vue : Power BI trace une courbe
continue en découpant sur `nature`, là où deux vues auraient imposé une relation de
plus pour rien.

### `vw_pbi_classement` — une ligne par élément classé

| Colonne | Description |
|---|---|
| `rang`, `element`, `valeur` | le classement |
| `part_pct` | **calculée** : part de l'élément dans le total |

### `vw_pbi_anomalies` — une ligne par observation aberrante

| Colonne | Description |
|---|---|
| `date_observation`, `valeur` | l'observation |
| `ecart_zscore` | son écart en nombre d'écarts-types |
| `sens` | `au-dessus` / `en-dessous` |
| `moyenne_serie`, `ecart_type_serie`, `seuil_ecarts_types` | le contexte de la détection |

### `vw_pbi_alertes` — une ligne par alerte

| Colonne | Description |
|---|---|
| `niveau`, `criticite_rang` | seuls `eleve` et `critique` produisent une alerte |
| `date_creation`, `statut`, `date_traitement` | cycle de vie |
| **Calculés** | `delai_traitement_h`, `age_ouverte_jours`, `est_ouverte` |

### `vw_pbi_activite` — une ligne par événement

| Colonne | Description |
|---|---|
| `type_evenement` | `import` ou `configuration` |
| `date_evenement`, `source`, `type_source`, `libelle`, `statut` | le fait |

---

## 6. Relations à établir

Dans **Modèle**, créer ces relations **un-à-plusieurs**, à sens unique :

| De (1) | Vers (∗) | Clé |
|---|---|---|
| `vw_pbi_entreprises` | `vw_pbi_resultats` | `id_entreprise` |
| `vw_pbi_entreprises` | `vw_pbi_alertes` | `id_entreprise` |
| `vw_pbi_entreprises` | `vw_pbi_activite` | `id_entreprise` |
| `vw_pbi_resultats` | `vw_pbi_series` | `id_resultat` |
| `vw_pbi_resultats` | `vw_pbi_classement` | `id_resultat` |
| `vw_pbi_resultats` | `vw_pbi_anomalies` | `id_resultat` |

Power BI proposera aussi de relier `vw_pbi_entreprises` aux trois vues de détail par
`id_entreprise` : **refuser**. Ces relations seraient redondantes avec le chemin qui
passe par `vw_pbi_resultats` et créeraient des ambiguïtés de filtrage.

**Table de dates.** Pour tout usage temporel sérieux, ajouter une table de dates
marquée comme telle et la relier à `date_execution`, `date_point`, `date_creation` et
`date_evenement` — une seule relation active à la fois, les autres en `USERELATIONSHIP`.

---

## 7. Visualisations suggérées

| Vue | Visuel | Réglage |
|---|---|---|
| `vw_pbi_resultats` | **Cartes** | `valeur_analysee`, `valeur_prevue`, `ecart_pct` |
| | **Graphique en anneau** | répartition par `criticite` |
| | **Table** | filtrée sur `conclusif = 0`, pour isoler les analyses non concluantes |
| `vw_pbi_series` | **Courbe** | axe `date_point`, valeur `valeur`, légende `nature` |
| | **Courbe + zone d'incertitude** | ajouter `borne_basse`/`borne_haute` en aires |
| `vw_pbi_classement` | **Barres horizontales** | axe `element`, valeur `valeur`, tri par `rang` |
| | **Treemap** | taille `part_pct` |
| `vw_pbi_anomalies` | **Nuage de points** | axe `date_observation`, valeur `valeur`, taille `ecart_zscore` |
| `vw_pbi_alertes` | **Histogramme empilé** | axe `date_creation` (mois), légende `niveau` |
| | **Jauge** | `delai_traitement_h` moyen |
| `vw_pbi_activite` | **Courbe** | événements par mois, légende `type_evenement` |

**Couleurs.** Reprendre celles de l'application pour que les rapports et l'interface
se lisent ensemble : `normal` `#64748b` · `attention` `#f59e0b` · `eleve` `#fb923c` ·
`critique` `#f87171`. Le vert reste réservé aux confirmations de succès.

⚠️ **Ne pas recréer en DAX les indicateurs déjà calculés** (`ecart_pct`,
`largeur_relative`, `part_pct`, `delai_traitement_h`, `age_*`). Une formule écrite une
fois en SQL ne peut pas diverger d'un rapport à l'autre.

---

## 8. ⚠️ Limite technique connue : longueur des séries

Le SGBD est **MariaDB 10.4**. `JSON_TABLE` — la façon standard d'éclater un tableau
JSON en lignes — n'existe qu'à partir de **MariaDB 10.6**. Le contournement retenu est
le moteur **SEQUENCE** (`seq_0_to_N`), une table virtuelle sans stockage, utilisable
dans une vue.

**La borne de cette séquence tronque en silence.** Mesuré :

| Borne | Série de 5 000 points | Verdict |
|---|---|---|
| `seq_0_to_999` | **1 000 lignes, aucune erreur** | ⚠️ perte silencieuse |
| `seq_0_to_9999` | 5 000 lignes | correct |

La borne est donc fixée à **10 000 points par série** (`LIMITE_POINTS_SERIE`). Deux
conséquences à connaître :

1. **`vw_pbi_resultats.serie_tronquee` vaut 1** si une série dépasse la borne. **Placer
   ce champ dans un visuel de contrôle** : sans lui, un rapport perdrait des points sans
   le dire. C'est le seul garde-fou.
2. **Le coût d'extraction est quadratique** : chaque point relit le document JSON.
   Mesuré — 39 points : 3 ms · 1 000 points : ~0,6 s · 5 000 points : ~16 s. La borne
   de la séquence, elle, ne coûte rien : `seq_0_to_99999` lit les données réelles aussi
   vite que `seq_0_to_999` (5 ms), le moteur ne matérialisant que les rangs utiles.

**Si les séries devaient dépasser quelques milliers de points**, la bonne réponse n'est
pas d'élever la borne mais de **migrer vers MariaDB 10.6+ et de remplacer le
`JOIN seq_` par `JSON_TABLE`**, qui parcourt le document une seule fois.

Sur les données actuelles, la série la plus longue compte **39 points**.

---

## 9. Rafraîchissement

En mode Importer, **Accueil → Actualiser** relit les vues. Le calcul reste fait par
l'application : les vues ne font que mettre à plat ce que le moteur d'analyse a déjà
produit. Un rapport actualisé reflète donc les analyses lancées depuis la dernière
actualisation, sans jamais recalculer quoi que ce soit.
