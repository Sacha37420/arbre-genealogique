# Arbre généalogique

Application d'affichage et d'édition d'arbres généalogiques : Django + DRF au dos,
Angular 21 devant, SSO Keycloak, PostgreSQL.

- Backend : http://localhost:8090 — documentation OpenAPI sur `/api/docs/`
- Frontend : http://localhost:4208

```bash
bash ../setup2.sh arbre-genealogique --yes
```

---

## 1. Modèle de données

Le socle suit **FamilySearch GEDCOM 7.0** ([gedcom.io](https://gedcom.io/specifications/FamilySearchGEDCOMv7.html)),
le seul format d'échange universel en généalogie : un fichier exporté ici se
réimporte dans Gramps, Ancestry, Geneanet ou webtrees.

| Enregistrement GEDCOM | Modèle Django | Rôle |
|---|---|---|
| `INDI` | `Individual` + `PersonalName` | Une personne, ses noms (naissance, mariage, alias…) |
| `FAM` | `Family` + `FamilySpouse` + `FamilyChild` | **Seul porteur des liens** : `HUSB`/`WIFE`/`CHIL`, avec `PEDI` (biologique, adopté, accueil) et `STAT` (prouvé, contesté) |
| événements/attributs | `Event` | `BIRT`, `DEAT`, `MARR`, `OCCU`, `RESI`… + `EVEN`/`FACT` pour tout le reste |
| `PLAC` | `Place` | Lieu hiérarchisé, géocodable (latitude/longitude) |
| `SOUR` / `REPO` | `Source`, `Repository`, `Citation` | Sources, dépôts, citations avec fiabilité (`QUAY` 0–3) |
| `OBJE` | `MediaObject` + `MediaLink` | Photos **stockées en base** (`BinaryField`), avec cadrage `CROP` |
| `SNOTE` | `SharedNote` | Notes partagées |
| `ASSO` | `Association` | Liens non familiaux : parrain, témoin, voisin… |

### Les dates, cas particulier

GEDCOM n'impose pas une date exacte : `ABT 1875`, `BET 1830 AND 1835`, `FROM 1901 TO 1918`,
`(vers la Révolution)`. Chaque événement conserve donc **la forme brute** (`date_raw`,
fidèle à la source) **et une forme analysée** (`date_start`, `date_end`, `date_precision`,
`date_modifier`) qui permet de trier et de placer l'événement sur la frise sans
inventer une précision que la source ne donne pas. Voir [`backend/api/gedcom.py`](backend/api/gedcom.py).

### Extensions hors GEDCOM

GEDCOM décrit les faits, pas leur mise en scène. S'y ajoutent donc :

| Modèle | Rôle |
|---|---|
| `NodeStyle` | Apparence d'une carte : fond, bordure, typographie, ombre, forme et taille de la photo |
| `StyleRule` | Style **conditionnel** : « si sexe = F → bordure rose », « si décédé → grisé ». Condition en JSON, évaluée côté serveur |
| `EdgeStyle` | Apparence des liens, par nature (filiation, union, adoption, association) |
| `NodeLayout` / `FamilyLayout` | Position d'une carte sur le canevas + `pinned` : une carte déplacée à la main n'est plus recalculée automatiquement |
| `CardTemplate` | Gabarit de la mini-carte et de la grande carte : photo, champs, ordre, typographie, frise |
| `TreeViewSettings` | Algorithme de placement, orientation, espacement, zoom, styles par défaut |
| `CustomFieldDef` | Champs définis par l'utilisateur, au-delà de GEDCOM |
| `EnrichmentMatch` | Candidats venus des sources externes, à accepter ou rejeter |

---

## 2. Sources externes (`/api/enrich/*`)

**Les clés d'API ne sont jamais stockées côté serveur.** Elles accompagnent chaque
requête (`credentials` dans le corps, ou en-tête `X-Provider-Key`) ; le backend les
relaie au fournisseur et les oublie. Côté navigateur, elles restent en `localStorage`.

| Source | Clé | Couverture |
|---|---|---|
| **WikiTree** | aucune | Arbre mondial collaboratif (~40 M de profils). Fonctionne sans inscription |
| **Wikidata** | aucune | Personnalités publiques, parenté structurée, portraits libres (Wikimedia Commons) |
| **FamilySearch** | `access_token` (OAuth 2.0) | Milliards d'actes indexés : état civil, registres paroissiaux, recensements |
| **Geni** | `access_token` | World Family Tree |
| **MyHeritage** | `access_token` | Lecture des arbres accessibles au jeton. *La recherche du catalogue n'est pas exposée par l'API Family Graph — elle est déclarée indisponible plutôt que simulée* |
| **Nominatim / Geoapify** | aucune / `geoapify_key` | Géocodage des lieux |

| Endpoint | Rôle |
|---|---|
| `GET /api/enrich/providers/` | Sources disponibles, clés attendues, façon de les obtenir (l'interface construit son formulaire à partir de là) |
| `POST /api/enrich/search/` | Recherche par nom, années, lieu — résultats normalisés et notés |
| `POST /api/enrich/fetch/` | Fiche complète, proches compris |
| `POST /api/enrich/import/` | Verse la fiche dans l'arbre : crée ou **complète** une personne (sans écraser l'existant), crée les proches annoncés et les relie, télécharge le portrait |

Ajouter une source = écrire une classe `Provider` dans `backend/api/providers/` et
l'inscrire dans le registre : les endpoints et l'interface la découvrent seuls.

---

## 3. Interface

| Page | Contenu |
|---|---|
| **`/` — Arbre** | Mini-cartes reliées par des liens. Placement automatique (dagre), zoom, déplacement, cartes déplaçables à la souris (et alors épinglées). Un clic ouvre la grande carte |
| **`/parametrage`** | Édition des deux gabarits, avec aperçu en direct : position et forme de la photo, champs affichés, ordre, gras/majuscules/taille/couleur/préfixe, format des dates, contenu de la frise |
| **`/recherche`** | Sources externes, saisie des clés d'API, recherche, fiche détaillée avec les proches, import dans l'arbre (nouvelle personne ou complément d'une personne existante) |

**Mini-carte** (défaut) : photo à gauche, puis nom, prénom, date de naissance et
date de décès (masquée pour les vivants).
**Grande carte** (défaut) : photo en haut à gauche, mêmes informations à sa droite,
et en dessous la frise chronologique de la vie — périodes (métiers, résidences) en
barres, événements ponctuels en points, chacun cliquable pour son détail.

---

## Import / export GEDCOM

```
POST /api/trees/<id>/import_gedcom/   (multipart « file », ou JSON « content »)
GET  /api/trees/<id>/gedcom/          → fichier .ged
```

L'import se fait en deux passes (individus puis familles) : sans cela, un `CHIL`
pointant vers un individu défini plus loin dans le fichier serait perdu.
