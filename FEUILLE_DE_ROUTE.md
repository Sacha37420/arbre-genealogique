# Feuille de route — arbre-genealogique

Ce fichier est lu par une machine : `validate-app.sh` refuse de déployer s'il manque ou s'il ne
contient aucun lot au format ci-dessous, et `app-test.sh` lit la commande de test. Garder les titres
tels quels.

App livrée avant l'obligation de feuille de route : le Lot 1 résume l'existant. Les lots suivants
viennent de l'analyse du 2026-10-31 ; la vue d'ensemble est dans `to_do_global.md`, à la racine du
dépôt du lab. Légende : [C] constaté, [D] déduit.

**Pas de test de cloisonnement navigateur, à dessein** : l'app n'exige aucun groupe. Sa protection
tient entièrement dans l'API, par arbre (propriétaire, partages lecteur/éditeur, arbre public), et
ses tests backend la vérifient. La ligne suivante est l'exemption déclarée que lit `validate-app.sh`
(réservée aux apps du superutilisateur) :

cloisonnement : aucun — l'app n'exige aucun groupe ; la protection est dans l'API, par arbre, tenue par ses tests backend

## Commande de test
python manage.py test

## Lot 1 — Existant : arbres, GEDCOM, fournisseurs externes, médias dans storage   ·   statut : livré
**Objectif :** arbre généalogique avec import GEDCOM, cartes personnalisables et fournisseurs
externes (WikiTree, Wikidata, Geni, FamilySearch, INSEE…). Les médias sont dans `storage`, l'arbre se
partage en lecteur ou en éditeur.
**Tâches :**
- [x] Fonctionnalités en service ; médias migrés vers `storage` (purge par signal `post_delete`)
- [x] Correctifs du chantier tests, deux tours (`chantiers/tests/rapports/carto-arbre.md`)
**Tests à passer avant de clore le lot :**
- [x] 155 tests backend (dont 66 d'autorisation par arbre)
**Vérifié le :** 2026-10-31 — relevé par lecture (version en service = `8a20033`), tests non rejoués.

## Lot 2 — Le code en service vient de l'image, pas du dossier   ·   statut : livré
**Objectif :** un redémarrage ne met plus en service le contenu du dossier sans tests.
**Constat [C] :** `docker-compose.yml:23-24` monte `./backend:/app`. C'est le piège retiré
d'app-builder (CLAUDE.md). **P1.**
**Tâches :**
- [x] Retirer le montage (`docker-compose.yml`) ; le Dockerfile copie déjà le code (`COPY . .`), aucun autre service ne montait le dossier
- [x] Reconstruire l'image, la tester (`app-test.sh`), puis `up -d --no-build` (architecte, à l'intégration) — fait le 2026-10-31, voir « Mis en service » ci-dessous
**Tests à passer avant de clore le lot :**
- [x] `docker inspect arbre-genealogique-backend` ne montre aucun montage sur `/app` (2026-10-31 : `Mounts=[]`)
- [x] Suite backend verte
**Vérifié le :** 2026-10-31 — `docker compose config` : aucun `/app` ; `bash chantiers/tests/essai.sh arbre-genealogique` → 184 tests OK. Non redéployé. **Mis en service le 2026-10-31** (chantier « urgences ») : image construite pendant que l'ancienne tournait, `scripts/app-test.sh` vert sur cette image, puis `up -d --no-build` ; test de cloisonnement par `lab-runner` vert après déploiement.

## Lot 3 — Limite de taille des médias   ·   statut : livré
**Objectif :** un compte ne peut pas remplir `storage`.
**Constat [D] :** aucune limite dans `api/views.py` ni dans `config/settings.py`. N'importe quel compte
du realm a accès à l'app.
**Tâches :**
- [x] Taille maximale (multipart, base64 et import fournisseur) : réponse 413 avant tout appel à `storage` (`api/limits.py`) ; `Content-Length` contrôlé avant toute lecture du corps ; base64 : taille décodée calculée sur la longueur du texte, avant décodage ; dernier filet dans `storage_client.upload_bytes()`
- [x] Ⓟ Valeur de la limite : **50 Mo** par fichier (`MEDIA_MAX_BYTES`, défaut 52428800, non ajoutée au `.env`) — tranchée par l'architecte (chantier « urgences », contrat v1 § 7), révisable par le propriétaire. `DATA_UPLOAD_MAX_MEMORY_SIZE` = base64 de la limite + 1 Mo
**Tests à passer avant de clore le lot :**
- [x] Taille limite acceptée ; un octet de plus → 413, et rien n'est émis vers `storage` (sur le modèle de traitement-de-fichiers-compils) — `api/tests/test_limites.py`, trois voies, plus 50 Mo réels en multipart
**Vérifié le :** 2026-10-31 — `bash chantiers/tests/essai.sh arbre-genealogique` → 184 tests OK (dont 19 de `test_limites`). Non redéployé. **Mis en service le 2026-10-31** (chantier « urgences ») : image construite pendant que l'ancienne tournait, `scripts/app-test.sh` vert sur cette image, puis `up -d --no-build` ; test de cloisonnement par `lab-runner` vert après déploiement.

## Lot 4 — Contrôles laissés par le chantier tests   ·   statut : à faire
**Objectif :** solder les points « à l'attention de l'architecte » de `carto-arbre.md`.
**Tâches :**
- [ ] Chercher en base les références croisées entre arbres créées avant le correctif D3 (lecture seule), puis les corriger avec accord
- [x] Ⓟ Faire tourner `KEYCLOAK_ADMIN_CLIENT_SECRET` : il a été affiché dans un terminal pendant le chantier — fait le 2026-11-01 (chantier « failles », F16 de `to_do_global.md`)
- [ ] Ⓟ Donner une règle au drapeau `Individual.confidential` : il est exposé tel quel, aucune règle dans le code
**Tests à passer avant de clore le lot :**
- [ ] Requête de contrôle : 0 ligne
- [ ] Après rotation, l'envoi de média fonctionne toujours
**Vérifié le :**

## Lot 5 — Jeton hors des URL d'images   ·   statut : livré
**Objectif :** le JWT ne part plus dans l'historique, les journaux ni le `Referer`.
**Constat [C] :** `?token=` dans `core/api.service.ts:48-56` et `backend/api/auth_extra.py:19`.
Dépend du chantier T4 (`to_do_global.md`) et du lot 2 de `storage`.
**Tâches :**
- [x] Images chargées par `fetch` + `Blob`, ou ticket court ; retrait de `auth_extra.py`
**Tests à passer avant de clore le lot :**
- [x] Aucune requête d'image ne porte `token=` ; les photos d'un arbre privé s'affichent ; un étranger reçoit 403 ; média lu par son propriétaire par `fetch` + en-tête, refusé à un autre compte (404) et sans jeton (403) — vérifié le 2026-11-01 (lab-runner, `e2e_member`)
**Vérifié le :** 2026-11-01 — `essai.sh arbre-genealogique` → 201 tests OK : `?token=` valide sans en-tête refusé (403, comme tout appel sans en-tête : la classe d'authentification ne pose pas d'`authenticate_header`), en-tête du propriétaire → 200, étranger avec en-tête → 404 (média hors des arbres visibles) ; `grep -rnE "token=" frontend/src` vide ; `ng build` vert. Reste en navigateur réel : photos d'un arbre privé et d'un arbre public affichées. — non déployé
**Relecture du 2026-11-08 (nuit) :** passé à « livré » : toutes les cases sont cochées, le commit
`dc10ed5` est dans la version en service (`2af0170`, chantier « failles »), `backend/api/auth_extra.py`
n'existe plus et `grep -rn "token=" frontend/src` est vide.

## Lot 6 — Réimport GEDCOM et petites frictions   ·   statut : à faire
**Objectif :** des erreurs lisibles, aucun secret de fournisseur exposé à une XSS.
**Tâches :**
- [ ] Réimporter un GEDCOM donne aujourd'hui un 400 avec l'erreur brute de PostgreSQL : le remplacer par un message explicite. Ⓟ Fusion avec renumérotation des `@I1@` : à décider.
- [x] Ne plus garder les identifiants des fournisseurs en `localStorage` (`pages/search/search.component.ts:120,129,266`)
- [ ] Couleur d'arête du graphe et couleur de repli tirées des tokens (`pages/tree/tree.component.ts:42`, `shared/cards/full-card.component.ts:23`)
**Tests à passer avant de clore le lot :**
- [ ] Un second import du même fichier donne un message lisible et aucun doublon
- [ ] Le thème sombre est vérifié dans `lab-runner`
**Vérifié le :** 2026-11-01 — case localStorage seule : coffre serveur `/api/provider-credentials/<fournisseur>/` (chiffré par `VAULT_ENCRYPTION_KEY`), 17 tests `test_coffre` dans 201 OK, `makemigrations --check` « No changes detected » ; `localStorage.removeItem('arbre.credentials')` à l'ouverture, sans envoi. — non déployé
**Relecture du 2026-11-08 (nuit) :** remis à « à faire » (un seul lot « en cours » par app, c'est le lot 7) ;
la case `localStorage` reste faite. Couleurs toujours en dur : `pages/tree/tree.component.ts:41`
(`'#b6c0cd'`), `shared/cards/full-card.component.ts:24` (`'#fff'`).

## Lot 7 — Gabarit front, documentation et chemin à portes   ·   statut : en cours
**Objectif :** même comportement que le gabarit corrigé (T3), un README juste, un déploiement par le moteur (T1).
**Tâches :**
- [x] Polices en chemin relatif (`styles.scss:3-11`) ; `redirectUri` sur la page courante (`keycloak.service.ts:32,37`) ; `tokenBases()` ; PKCE
- [x] README : la l. 28 dit encore que les photos sont stockées en base (`BinaryField`) ; décrire `storage`, les partages et les fournisseurs
- [ ] Ligne `.owners`, une fois l'exemption de cloisonnement disponible dans `validate-app.sh`
**Tests à passer avant de clore le lot :**
- [x] `grep -n BinaryField README.md` ne rend rien
- [x] `validate-app.sh arbre-genealogique --quality-only` vert (avertissement d'exemption de cloisonnement, voulu)
**Vérifié le :** 2026-11-01 — cases gabarit front et README : `ng build` vert ; `grep -n BinaryField README.md` vide. `.owners` et `validate-app.sh --quality-only` non faits. — non déployé

**Relecture du 2026-11-08 (nuit) :** `grep -n BinaryField README.md` vide ; `validate-app.sh --quality-only`
joué sur une copie de cette feuille de route (outil `valq.sh` du chantier de nuit) : code 0, avec
l'avertissement d'exemption. Reste la ligne `.owners`.

## Mise en service — chantier « failles » (2026-11-01)
Les mentions « non déployé » ci-dessus datent d'avant cette mise en service. Image construite pendant
que l'ancienne tournait, `scripts/app-test.sh` : 201 tests OK, puis `up -d --no-build` ; ports
publiés sur `127.0.0.1` ; client Keycloak passé en PKCE S256 exigé, URI de retour réduites à celles de
l'app (`create-app-client.sh --pkce --prune-uris`) ; cloisonnement : aucun test navigateur à dessein (protection dans l'API).
Restent les vérifications en navigateur réel notées dans les lots (images, coffres de clés, envois).
