"""Client minimal de l'API fichiers de l'app storage.

Le backend agit via le **service account** du client confidentiel compagnon
``arbre-genealogique-admin`` (flux ``client_credentials``, créé par
la simple présence de ``.keycloak-service-account-roles`` — voir ce fichier :
aucun rôle Keycloak n'y est demandé, cette app n'administre pas le realm).

Pourquoi un compte de service plutôt que le forward du token de l'utilisateur
courant (variante ``carto-lab``) : **l'autorisation est portée par les modèles
de cette app**, pas par storage. Un média appartient à un ``Tree``, dont l'accès
dépend de ``Tree.owner_email``, de ``Tree.is_public`` et des ``TreeShare`` (accès
par email, ouvert avant même que la personne se soit connectée) — trois notions
que storage ignore totalement. ``OwnedTreeMixin`` (``visible_trees`` /
``editable_trees``) décide donc *avant* d'appeler ce module, qui reste un simple
transport.

Un unique partage (``settings.STORAGE_NAMESPACE``) pour toute l'app, pas un par
arbre ni par propriétaire : les noms de partage sont des slugs (``[a-z0-9-]``,
cf. ``storage/backend/api/validators.py``) et les propriétaires sont identifiés
par leur email — il n'y a pas de dérivation évidente et stable de l'un vers
l'autre. Le cloisonnement reste entièrement assuré par les requêtes de
``api/views.py``, exactement comme quand les octets étaient dans ``devdb``.

Le partage doit exister au préalable, côté storage ::

    python manage.py create_group_share arbre-genealogique \\
        --owner sacha --required-groups developers

et le compte de service y être autorisé via
``KEYCLOAK_SERVICE_WRITE_SHARES=arbre-genealogique-admin:arbre-genealogique``
(``storage/.env``).
"""
from __future__ import annotations

import io
import re
import time
from uuid import uuid4

import requests
from django.conf import settings

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


def _files_url(suffix: str = '') -> str:
    return (
        f'{settings.STORAGE_INTERNAL_URL}/api/files/'
        f'{settings.STORAGE_NAMESPACE}/{suffix}'
    )


def upload(relative_path: str, fileobj, filename: str, content_type: str = '') -> dict:
    """Dépose `fileobj` sous `relative_path`. Écrase un fichier déjà présent au
    même chemin. Retourne le JSON de StoredFileSerializer (voir
    storage/backend/api/views.py)."""
    files = {'file': (filename, fileobj, content_type or 'application/octet-stream')}
    try:
        resp = requests.post(
            _files_url(), headers=_headers(),
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


def upload_bytes(blob: bytes, filename: str, content_type: str = '') -> str:
    """Dépose `blob` sous un chemin neuf et retourne son `relative_path`.

    Point d'entrée unique des trois chemins d'écriture de l'app (upload
    multipart, base64 `data_base64`, import d'une photo depuis un fournisseur
    externe) : le nommage des chemins reste ainsi défini à un seul endroit.
    """
    nom = _safe_name(filename)
    relative_path = f'media/{uuid4().hex}-{nom}'
    return upload(relative_path, io.BytesIO(blob), nom, content_type)['relative_path']


def download(relative_path: str) -> requests.Response:
    """Retourne la réponse streamée (`stream=True`) — à l'appelant de la
    consommer et de la fermer (cf. MediaObjectViewSet.file)."""
    try:
        resp = requests.get(
            _files_url(f'content/{relative_path}'), headers=_headers(),
            stream=True, timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code != 200:
        raise StorageClientError(f'Téléchargement storage échoué (HTTP {resp.status_code}).')
    return resp


def delete(relative_path: str) -> None:
    try:
        resp = requests.delete(
            _files_url(f'content/{relative_path}'), headers=_headers(), timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code not in (204, 404):
        raise StorageClientError(f'Suppression storage échouée (HTTP {resp.status_code}).')
