"""Client minimal de l'API fichiers de l'app storage.

Le backend agit via le **service account** du client confidentiel compagnon
``arbre-genealogique-admin`` (flux ``client_credentials``, créé par
la simple présence de ``.keycloak-service-account-roles`` — voir ce fichier :
aucun rôle Keycloak n'y est demandé, cette app n'administre pas le realm).

Pourquoi un compte de service plutôt que le forward du token de l'utilisateur
courant (variante ``carto-lab``) pour les *écritures* : **l'autorisation des
écritures est portée par les modèles de cette app**, pas par storage — un
média appartient à un ``Tree``, dont l'accès en édition dépend de
``Tree.owner_email`` et du rôle EDITOR dans ``TreeShare``. ``OwnedTreeMixin``
décide donc *avant* d'appeler ce module, qui reste un simple transport pour
l'upload/suppression.

── Un partage storage par arbre, pas un seul partage global ──────────────────

Historiquement (première migration vers storage), toute l'app partageait un
unique namespace ``settings.STORAGE_NAMESPACE`` et le cloisonnement était
*entièrement* porté par le proxy (``MediaObjectViewSet.file``) : storage
ignorait tout de ``Tree``/``TreeShare``. Ce module réplique maintenant
``TreeShare`` en de vrais ``Share``/``ShareMember`` par arbre
(``namespace_for_tree``), pour permettre un **accès direct frontend → storage**
signé par le propre token de l'utilisateur (plus de proxy pour la lecture,
cf. ``media_url``) : c'est cette réplication qui rend l'accès direct sûr,
storage devenant lui-même le rempart.

Convention de nommage : ``<STORAGE_NAMESPACE>-tree-<id>`` (ex.
``arbre-genealogique-tree-2``). Pas d'``<owner_username>`` dans le nom : un
``Tree`` n'a pas de username Keycloak stable (seulement ``owner_email``, sans
dérivation évidente vers un username), et l'id de l'arbre suffit déjà à
l'unicité — même choix que ``conciergerie-bien-<id>`` (voir CLAUDE.md racine).
Le nom reste un slug plat ; une hiérarchie future (chantier fondation
``storage/``) pourra rattacher ce partage à un parent via une vraie relation
en base (``Share.parent``), sans avoir besoin de renommer quoi que ce soit.

Chaque partage est créé et administré par le compte de service lui-même
(``owner_username`` = ``service:arbre-genealogique-admin`` côté storage) via
``POST /api/shares/`` — pas via le mécanisme de préfixe auto-vivifiant
(``KEYCLOAK_SERVICE_WRITE_PREFIXES``) seul, car ``ShareMember`` doit pouvoir
être peuplé (propriétaire de l'arbre, invités ``TreeShare``) **avant** même
qu'un premier fichier existe — ``resolve_namespace`` n'auto-crée le partage
qu'au premier upload, trop tard pour ``POST /api/shares/<name>/members/``
(qui exige un partage déjà existant). ``KEYCLOAK_SERVICE_WRITE_PREFIXES``
reste néanmoins nécessaire : c'est lui qui autorise l'upload/téléchargement
sur ces namespaces (``resolve_namespace`` pour un compte de service) ::

    KEYCLOAK_SERVICE_WRITE_PREFIXES=…,arbre-genealogique-admin:arbre-genealogique-tree-

── Cas ``Tree.is_public`` — pas de partage direct pour ce cas ────────────────

Un arbre public est visible sans figurer dans ``TreeShare`` : storage n'a
aucune façon d'exprimer « tout compte authentifié de cette app », car
``arbre-genealogique`` n'a **aucun** ``--require-group`` (n'importe quel
compte du realm peut se connecter à l'app, cf. ``.keycloak-client-opts``) —
il n'existe donc aucun groupe LDAP réel correspondant à « les utilisateurs de
cette app ». Inventer un tel groupe serait un mécanisme d'infra non prévu par
le lab (cf. CLAUDE.md, « Partage lié à un groupe Keycloak ») : ce module ne le
fait pas. Pour un arbre public, ``media_url`` renvoie donc toujours l'URL du
proxy (``MediaObjectViewSet.file``, authentifié par n'importe quel compte du
realm grâce à ``KeycloakQueryTokenAuthentication`` — la même barrière que les
arbres privés avant cette migration). Voir le rapport de migration pour la
discussion complète de ce choix.
"""
from __future__ import annotations

import io
import logging
import re
import time
from uuid import uuid4

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 30


class StorageClientError(RuntimeError):
    """Erreur de communication ou de configuration avec l'API storage."""


def _split_issuer() -> tuple[str, str]:
    issuer = (settings.KEYCLOAK_ISSUER_URI or '').rstrip('/')
    if '/realms/' not in issuer:
        raise StorageClientError(
            f"KEYCLOAK_ISSUER_URI invalide : {issuer!r} (attendu …/realms/<realm>)."
        )
    base, realm = issuer.split('/realms/', 1)
    return base, realm.split('/', 1)[0]


# Cache de token au niveau process (chaque worker gunicorn a le sien).
_token_cache: dict[str, float | str] = {'value': '', 'exp': 0.0}


def _token() -> str:
    now = time.time()
    if _token_cache['value'] and float(_token_cache['exp']) > now + 5:
        return str(_token_cache['value'])

    client_id = getattr(settings, 'KEYCLOAK_ADMIN_CLIENT_ID', '') or ''
    secret = getattr(settings, 'KEYCLOAK_ADMIN_CLIENT_SECRET', '') or ''
    if not client_id or not secret:
        raise StorageClientError(
            "KEYCLOAK_ADMIN_CLIENT_ID / KEYCLOAK_ADMIN_CLIENT_SECRET manquants. "
            "Lancez setup2.sh arbre-genealogique (provisionne le "
            "client arbre-genealogique-admin)."
        )

    base, realm = _split_issuer()
    try:
        resp = requests.post(
            f'{base}/realms/{realm}/protocol/openid-connect/token',
            data={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': secret,
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'Keycloak injoignable : {exc}') from exc
    if resp.status_code != 200:
        raise StorageClientError(
            f"Auth service account échouée (HTTP {resp.status_code}) : {resp.text[:200]}"
        )
    data = resp.json()
    _token_cache['value'] = data['access_token']
    _token_cache['exp'] = now + int(data.get('expires_in', 60))
    return str(_token_cache['value'])


def _headers() -> dict[str, str]:
    return {'Authorization': f'Bearer {_token()}'}


# ─────────────────────────────────────────────────────────────────────────────
# Namespace par arbre
# ─────────────────────────────────────────────────────────────────────────────

_TREE_PREFIX = f'{settings.STORAGE_NAMESPACE}-tree-'


def namespace_for_tree(tree_id: int) -> str:
    """Nom du partage storage dédié à cet arbre — voir le docstring du module."""
    return f'{_TREE_PREFIX}{tree_id}'


# ─────────────────────────────────────────────────────────────────────────────
# Fichiers
# ─────────────────────────────────────────────────────────────────────────────

def _files_url(namespace: str, suffix: str = '') -> str:
    return f'{settings.STORAGE_INTERNAL_URL}/api/files/{namespace}/{suffix}'


def upload(relative_path: str, fileobj, filename: str, content_type: str = '',
           namespace: str | None = None) -> dict:
    """Dépose `fileobj` sous `relative_path` dans `namespace` (défaut : l'ancien
    partage global, pour compatibilité). Écrase un fichier déjà présent au même
    chemin. Retourne le JSON de StoredFileSerializer (voir storage/backend/api/views.py)."""
    namespace = namespace or settings.STORAGE_NAMESPACE
    files = {'file': (filename, fileobj, content_type or 'application/octet-stream')}
    try:
        resp = requests.post(
            _files_url(namespace), headers=_headers(),
            data={'relative_path': relative_path}, files=files, timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code != 201:
        raise StorageClientError(
            f'Upload storage échoué (HTTP {resp.status_code}) : {resp.text[:200]}'
        )
    return resp.json()


def _safe_name(nom: str) -> str:
    """Nom de fichier sûr pour un segment de chemin storage : ni '/', ni segment
    vide, ni '.'/'..' (cf. storage/backend/api/validators.py). Le nom d'origine
    reste intact dans MediaObject.filename."""
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', (nom or '').strip()) or 'media'
    return cleaned.strip('.') or 'media'


def upload_bytes(blob: bytes, filename: str, content_type: str = '',
                  namespace: str | None = None) -> str:
    """Dépose `blob` sous un chemin neuf dans `namespace` et retourne son
    `relative_path` (sans le namespace : celui-ci se retrouve à partir de
    `MediaObject.tree_id`, jamais stocké en base — cf. `namespace_for_tree`).

    Point d'entrée unique des trois chemins d'écriture de l'app (upload
    multipart, base64 `data_base64`, import d'une photo depuis un fournisseur
    externe) : le nommage des chemins reste ainsi défini à un seul endroit.
    """
    nom = _safe_name(filename)
    relative_path = f'media/{uuid4().hex}-{nom}'
    return upload(relative_path, io.BytesIO(blob), nom, content_type, namespace=namespace)['relative_path']


def download(relative_path: str, namespace: str | None = None) -> requests.Response:
    """Retourne la réponse streamée (`stream=True`) — à l'appelant de la
    consommer et de la fermer (cf. MediaObjectViewSet.file)."""
    namespace = namespace or settings.STORAGE_NAMESPACE
    try:
        resp = requests.get(
            _files_url(namespace, f'content/{relative_path}'), headers=_headers(),
            stream=True, timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code != 200:
        raise StorageClientError(f'Téléchargement storage échoué (HTTP {resp.status_code}).')
    return resp


def delete(relative_path: str, namespace: str | None = None) -> None:
    namespace = namespace or settings.STORAGE_NAMESPACE
    try:
        resp = requests.delete(
            _files_url(namespace, f'content/{relative_path}'), headers=_headers(), timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code not in (204, 404):
        raise StorageClientError(f'Suppression storage échouée (HTTP {resp.status_code}).')


# ─────────────────────────────────────────────────────────────────────────────
# URL directe (lecture sans passer par le backend de cette app)
# ─────────────────────────────────────────────────────────────────────────────

def direct_url(namespace: str, relative_path: str) -> str | None:
    """URL storage complète, appelable telle quelle par le frontend avec son
    propre token — même origine que l'app (Caddy monte storage sous
    `/storage-api`), donc pas de CORS à gérer (cf. CLAUDE.md, « Verrou 2
    généralisé »). `None` si `settings.PUBLIC_DOMAIN` n'est pas configuré (dev
    local sans domaine, cf. `.env` DOMAIN=CHANGE_ME) : dans ce cas l'appelant
    doit retomber sur le proxy.
    """
    domain = getattr(settings, 'PUBLIC_DOMAIN', '') or ''
    if not domain or domain == 'CHANGE_ME':
        return None
    return f'https://{domain}/storage-api/api/files/{namespace}/content/{relative_path}'


def media_url(media, tree=None) -> str:
    """URL à renvoyer au frontend pour lire les octets de `media` (un
    `MediaObject`) — le point unique dont dépendent tous les sérialiseurs et
    `graph.py`, pour ne pas dupliquer cette décision à quatre endroits.

    `tree` : passer l'objet `Tree` déjà en mémoire quand l'appelant l'a déjà
    (ex. `graph.build_graph`, qui construit toutes les cartes d'un même arbre)
    pour éviter une requête `media.tree` par média ; sinon (sérialiseurs,
    objets isolés), retombe sur `media.tree` — à charger via `select_related`
    par l'appelant pour éviter le N+1 (cf. `MediaObjectViewSet.queryset`).

    - média externe (pas de `storage_path`) : son `external_url` tel quel.
    - arbre public (`Tree.is_public`) : proxy (`/api/media/<id>/file/`) —
      storage n'a aucun accès direct pour ce cas, voir le docstring du module.
    - arbre privé, `PUBLIC_DOMAIN` configuré : URL storage directe, signée par
      le token de l'utilisateur côté frontend.
    - arbre privé, `PUBLIC_DOMAIN` absent (dev local) : repli sur le proxy,
      pour ne rien casser en développement sans domaine.
    """
    if not media.storage_path:
        return media.external_url or ''
    tree = tree or media.tree
    proxy_url = f'/api/media/{media.pk}/file/'
    if tree.is_public:
        return proxy_url
    return direct_url(namespace_for_tree(tree.pk), media.storage_path) or proxy_url


# ─────────────────────────────────────────────────────────────────────────────
# Synchronisation Share/ShareMember — réplique TreeShare (+ propriétaire) dans
# storage, pour que l'accès direct (media_url ci-dessus) soit sûr : storage
# devient lui-même le rempart, plus seulement OwnedTreeMixin.
# ─────────────────────────────────────────────────────────────────────────────

def _shares_url(suffix: str = '') -> str:
    return f'{settings.STORAGE_INTERNAL_URL}/api/shares/{suffix}'


def ensure_share(namespace: str) -> None:
    """Crée le partage s'il n'existe pas encore (idempotent).

    Vérifie l'existence AVANT de tenter la création plutôt que de se fier au
    code HTTP renvoyé par un POST en double : au moment d'écrire ceci,
    storage/backend/api/views.py::ShareListCreateView.post laisse remonter la
    ValidationError levée par Share.full_clean() (contrainte d'unicité sur le
    nom, qui est la clé primaire) au lieu de la convertir en 400 comme le
    laisse penser le `except IntegrityError` du code — un second POST sur un
    nom déjà pris répond donc 500, pas 400 (constaté en conditions réelles
    lors de cette migration, à corriger côté storage — hors périmètre de cette
    app). `list_members` sert de sonde d'existence + de propriété (404/403 si
    le partage n'existe pas encore ou ne nous appartient pas).
    """
    try:
        list_members(namespace)
        return  # existe déjà et nous appartient : rien à faire.
    except StorageClientError:
        pass

    try:
        resp = requests.post(
            _shares_url(), headers=_headers(), data={'name': namespace}, timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code == 201:
        return

    # Filet de rattrapage : une course (deux requêtes concurrentes créant le
    # même partage) ou le bug de statut HTTP décrit ci-dessus peuvent faire
    # échouer le POST alors que le partage existe déjà bel et bien — revérifier
    # avant d'abandonner, plutôt que de dépendre d'un code HTTP précis.
    try:
        list_members(namespace)
        return
    except StorageClientError:
        pass
    raise StorageClientError(f'Création du partage storage échouée (HTTP {resp.status_code}) : {resp.text[:200]}')


def list_members(namespace: str) -> list[dict]:
    try:
        resp = requests.get(
            _shares_url(f'{namespace}/members/'), headers=_headers(), timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code != 200:
        raise StorageClientError(f'Lecture des membres storage échouée (HTTP {resp.status_code}).')
    return resp.json()


def set_member(namespace: str, email: str, role: str) -> None:
    try:
        resp = requests.post(
            _shares_url(f'{namespace}/members/'), headers=_headers(),
            data={'member_email': email, 'role': role}, timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code != 201:
        raise StorageClientError(f'Ajout de membre storage échoué (HTTP {resp.status_code}) : {resp.text[:200]}')


def remove_member(namespace: str, email: str) -> None:
    try:
        resp = requests.delete(
            _shares_url(f'{namespace}/members/{email}/'), headers=_headers(), timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code not in (204, 404):
        raise StorageClientError(f'Retrait de membre storage échoué (HTTP {resp.status_code}).')


def sync_tree_share(tree) -> None:
    """Aligne le partage storage de `tree` sur son état DB actuel (propriétaire
    + `TreeShare`).

    Recalcule l'ensemble complet des membres à chaque appel plutôt que
    d'incrémenter à partir de l'événement déclencheur (ajout/retrait d'un
    seul `TreeShare`) : plus simple à raisonner, et auto-corrige toute dérive
    d'un appel précédent resté partiel (ex. storage temporairement injoignable
    en cours de synchronisation — cf. TreeShareViewSet).

    Le propriétaire de l'arbre est toujours membre en écriture : c'est lui qui
    dépose les photos, et le partage storage appartient au compte de service
    (jamais à lui), donc sans cette ligne son propre token n'aurait aucun
    accès direct à ses propres médias.
    """
    namespace = namespace_for_tree(tree.pk)
    ensure_share(namespace)

    from .models import TreeShare  # import tardif : évite un cycle avec models.py

    wanted: dict[str, str] = {tree.owner_email: 'write'}
    for share in tree.shares.all():
        wanted[share.email] = 'write' if share.role == TreeShare.Role.EDITOR else 'read'

    existing = {m['member_email']: m['role'] for m in list_members(namespace)}

    for email, role in wanted.items():
        if existing.get(email) != role:
            set_member(namespace, email, role)
    for email in existing:
        if email not in wanted:
            remove_member(namespace, email)
