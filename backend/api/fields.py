"""
Champ chiffré au repos — le coffre des identifiants des fournisseurs.

`EncryptedTextField` chiffre chaque valeur avec la clé Fernet
`settings.VAULT_ENCRYPTION_KEY` (lue dans le `.env`, sans valeur par défaut) avant
de l'écrire en base, et la déchiffre à la lecture. La base, ses sauvegardes et
pgAdmin ne voient qu'un jeton Fernet (`gAAAAA…`).

Il n'y a jamais eu d'autre chiffrement dans cette app : une seule clé, pas de
`MultiFernet`. Une valeur qui ne se déchiffre pas :
  · si elle a la forme d'un jeton Fernet (`gAAAAA…`) — chiffrée sous une autre
    clé, ou abîmée — elle est traitée comme **absente** (`None`) et journalisée
    sans sa valeur : on ne rend jamais le texte chiffré à l'appelant ;
  · sinon, c'est une valeur en clair écrite hors de ce champ : rendue telle
    quelle (elle sera chiffrée à sa prochaine écriture).
"""
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

FERNET_PREFIX = 'gAAAAA'


def _fernet() -> Fernet:
    return Fernet(settings.VAULT_ENCRYPTION_KEY.encode())


class EncryptedTextField(models.TextField):
    """TextField chiffré par `VAULT_ENCRYPTION_KEY` (Fernet)."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None:
            return None
        return _fernet().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            if value.startswith(FERNET_PREFIX):
                logger.warning('Coffre : une valeur chiffrée ne se déchiffre pas sous '
                               'VAULT_ENCRYPTION_KEY (champ %s) — traitée comme absente.',
                               self.name)
                return None
            return value
