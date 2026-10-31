"""Taille maximale d'un média — un compte ne doit pas pouvoir remplir ``storage``.

Limite par fichier : ``settings.MEDIA_MAX_BYTES`` (défaut 50 Mo, réglable par la
clé ``MEDIA_MAX_BYTES`` du ``.env``). Elle s'applique aux trois chemins
d'écriture (envoi multipart, base64 ``data_base64``, portrait importé d'un
fournisseur) et répond **413 avant tout appel à storage**.

Deux étages :
  1. ``check_request_body`` — avant toute lecture du corps, d'après l'en-tête
     ``Content-Length`` : un corps plus gros que le plus gros envoi légitime
     (``body_max_bytes``) est refusé sans être lu ni écrit sur le disque.
  2. ``check_media_size`` — sur la taille exacte du fichier (taille de
     l'envoi multipart, taille décodée du base64, octets reçus du fournisseur).
"""
from __future__ import annotations

import base64
import binascii

from django.conf import settings
from rest_framework.exceptions import APIException, ValidationError

# Marge pour ce qui accompagne le fichier dans le corps : en-têtes multipart,
# autres champs (titre, description…), préfixe « data:…;base64, » et JSON.
BODY_OVERHEAD_BYTES = 1024 * 1024


class MediaTooLarge(APIException):
    status_code = 413
    default_code = 'media_too_large'

    def __init__(self, max_bytes: int | None = None):
        max_bytes = media_max_bytes() if max_bytes is None else max_bytes
        super().__init__({
            'detail': f'Fichier trop volumineux : {max_bytes} octets au plus.',
            'max_bytes': max_bytes,
        })


def media_max_bytes() -> int:
    return int(settings.MEDIA_MAX_BYTES)


def base64_length(n: int) -> int:
    """Longueur du base64 (avec remplissage) de ``n`` octets."""
    return -(-n // 3) * 4


def body_max_bytes(max_bytes: int | None = None) -> int:
    """Plus gros corps de requête légitime pour un média de ``max_bytes`` :
    sa forme base64 (la plus longue des deux) plus la marge."""
    max_bytes = media_max_bytes() if max_bytes is None else max_bytes
    return base64_length(max_bytes) + BODY_OVERHEAD_BYTES


def check_media_size(size: int) -> None:
    if size > media_max_bytes():
        raise MediaTooLarge()


def check_request_body(request) -> None:
    """Refuse d'après ``Content-Length``, avant que le corps soit lu."""
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except (TypeError, ValueError):
        length = 0
    if length > body_max_bytes():
        raise MediaTooLarge()


_WHITESPACE = {ord(c): None for c in ' \t\r\n\f\v'}


def decode_base64(raw: str) -> bytes:
    """Décode ``raw`` après avoir vérifié la taille décodée **sans décoder** :
    elle se déduit de la longueur du texte (4 caractères → 3 octets, moins le
    remplissage ``=``). Les blancs (retours à la ligne d'un base64 MIME) sont
    ignorés, comme le fait ``base64.b64decode`` ; tout autre caractère hors
    alphabet est refusé (400), sans quoi la taille annoncée serait fausse."""
    compact = raw.translate(_WHITESPACE)
    decoded = len(compact) // 4 * 3 - compact[-2:].count('=')
    check_media_size(decoded)
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError({'data_base64': 'Base64 invalide.'})
