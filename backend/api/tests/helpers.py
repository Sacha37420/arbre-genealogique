"""Outillage commun des tests : jetons signés localement, faux ``storage``, jeu de données.

Trois choses sont simulées, et rien d'autre :

* le **téléchargement du JWKS** de Keycloak (clé RSA locale) — signature,
  expiration, ``azp``, groupes et ``email`` sont vérifiés par le vrai code ;
* **l'API HTTP de ``storage`` et le jeton du compte de service**
  (``FakeStorage``, branché à la place de ``requests`` dans
  ``api.storage_client`` seulement) — tout ``storage_client`` s'exécute ;
* **tout autre appel réseau** : il échoue immédiatement
  (``requests.ConnectionError``), au lieu d'attendre un délai.
"""
from __future__ import annotations

import time
from unittest import mock

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api import storage_client
from api.authentication import KeycloakJWTAuthentication
from api.gedcom import parse_date
from api.models import (
    CardTemplate,
    Citation,
    CustomFieldDef,
    EdgeStyle,
    EnrichmentMatch,
    Event,
    Family,
    FamilyChild,
    FamilyLayout,
    FamilySpouse,
    ImportJob,
    Individual,
    MediaLink,
    MediaObject,
    NodeLayout,
    NodeStyle,
    PersonalName,
    Place,
    Repository,
    SharedNote,
    Source,
    StyleRule,
    Tree,
    TreeShare,
)

CLIENT_ID = 'arbre-genealogique'

# ── Jetons ────────────────────────────────────────────────────────────────────

RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
#: Une seconde clé, inconnue du « realm » : sert aux jetons mal signés.
FOREIGN_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class LocalJwks:
    """Tient lieu de ``PyJWKClient`` : rend la clé publique locale, sans réseau."""

    class _Key:
        key = RSA_KEY.public_key()

    def get_signing_key_from_jwt(self, token):
        return self._Key()


_ABSENT = object()


def make_token(email=_ABSENT, *, azp: str = CLIENT_ID, groups=None, exp_in: int = 300,
               key=RSA_KEY, username: str | None = None) -> str:
    """Jeton RS256 tel que Keycloak l'émettrait. ``email=None`` : sans claim email."""
    if email is _ABSENT:
        email = 'alice@example.org'
    claims = {'azp': azp, 'exp': int(time.time()) + exp_in}
    if email is not None:
        claims['email'] = email
        claims['preferred_username'] = username or email.split('@')[0]
    if groups is not None:
        claims['groups'] = groups
    return jwt.encode(claims, key, algorithm='RS256')


def client_for(email: str, **kwargs) -> APIClient:
    """Client portant un vrai jeton : chaque requête traverse l'authentification réelle."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {make_token(email, **kwargs)}')
    return client


# ── Faux storage ──────────────────────────────────────────────────────────────

class _Response:
    def __init__(self, status_code: int, payload=None, content: bytes = b'') -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = '' if payload is None else str(payload)
        self.headers = {}

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=65536):
        yield self.content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeStorage:
    """L'API de ``storage`` vue par ``storage_client``, en mémoire.

    Remplace l'objet ``requests`` du module : ``post``/``get``/``delete`` sont
    aiguillés selon l'URL. ``files[namespace][chemin]`` et
    ``members[namespace][email]`` sont l'état observable par les tests.
    ``down = True`` simule un storage injoignable.
    """

    RequestException = requests.RequestException

    def __init__(self) -> None:
        self.files: dict[str, dict[str, bytes]] = {}
        self.members: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.token_requests = 0
        self.down = False
        self.authorizations: list[str] = []

    # — aiguillage —
    def _route(self, method: str, url: str, **kwargs):
        self.calls.append((method, url))
        if '/protocol/openid-connect/token' in url:
            self.token_requests += 1
            return _Response(200, {'access_token': 'jeton-de-service', 'expires_in': 300})
        if self.down:
            raise requests.ConnectionError('storage arrêté (simulé)')
        self.authorizations.append((kwargs.get('headers') or {}).get('Authorization', ''))
        prefix = storage_client.settings.STORAGE_INTERNAL_URL + '/api/'
        assert url.startswith(prefix), f'URL storage inattendue : {url}'
        parts = url[len(prefix):].split('/')
        if parts[0] == 'files':
            return self._files(method, parts[1], '/'.join(parts[2:]), **kwargs)
        if parts[0] == 'shares':
            return self._shares(method, [p for p in parts[1:] if p], **kwargs)
        raise AssertionError(f'URL storage inattendue : {url}')

    def _files(self, method, namespace, rest, **kwargs):
        if method == 'POST' and rest == '':
            path = kwargs['data']['relative_path']
            fileobj = kwargs['files']['file'][1]
            self.files.setdefault(namespace, {})[path] = fileobj.read()
            return _Response(201, {'relative_path': path})
        assert rest.startswith('content/'), rest
        path = rest[len('content/'):]
        store = self.files.get(namespace, {})
        if method == 'GET':
            return _Response(200, content=store[path]) if path in store else _Response(404)
        if method == 'DELETE':
            return _Response(204) if store.pop(path, None) is not None else _Response(404)
        raise AssertionError((method, rest))

    def _shares(self, method, parts, **kwargs):
        if method == 'POST' and not parts:
            name = kwargs['data']['name']
            if name in self.members:
                return _Response(500)   # comportement réel de storage sur un doublon
            self.members[name] = {}
            return _Response(201, {'name': name})
        namespace = parts[0]
        assert parts[1] == 'members', parts
        if namespace not in self.members:
            return _Response(404)
        if method == 'GET':
            return _Response(200, [{'member_email': e, 'role': r}
                                   for e, r in self.members[namespace].items()])
        if method == 'POST':
            self.members[namespace][kwargs['data']['member_email']] = kwargs['data']['role']
            return _Response(201, {})
        if method == 'DELETE':
            self.members[namespace].pop(parts[2], None)
            return _Response(204)
        raise AssertionError((method, parts))

    def post(self, url, **kwargs):
        return self._route('POST', url, **kwargs)

    def get(self, url, **kwargs):
        return self._route('GET', url, **kwargs)

    def delete(self, url, **kwargs):
        return self._route('DELETE', url, **kwargs)

    # — lecture par les tests —
    def all_paths(self) -> set[tuple[str, str]]:
        return {(ns, p) for ns, store in self.files.items() for p in store}

    def writes(self) -> list[tuple[str, str]]:
        """Appels qui modifient storage (hors jeton de service)."""
        return [c for c in self.calls
                if c[0] in ('POST', 'DELETE') and 'openid-connect' not in c[1]]


def ns(tree) -> str:
    return storage_client.namespace_for_tree(tree.pk)


# ── Socle ─────────────────────────────────────────────────────────────────────

ALICE = 'alice@example.org'    # propriétaire
BOB = 'bob@example.org'        # invité « éditeur »
CAROL = 'carol@example.org'    # invitée « lecture »
DAVE = 'dave@example.org'      # étranger : compte du realm, aucun lien avec l'arbre


@override_settings(
    KEYCLOAK_CLIENT_ID=CLIENT_ID,
    KEYCLOAK_REQUIRED_GROUPS='',
    KEYCLOAK_ISSUER_URI='http://keycloak.test/realms/ssolab',
    KEYCLOAK_ADMIN_CLIENT_ID='arbre-genealogique-admin',
    KEYCLOAK_ADMIN_CLIENT_SECRET='secret-de-test',
    STORAGE_INTERNAL_URL='http://storage.test',
    PUBLIC_DOMAIN='lab.example.org',
)
class LabTestCase(TestCase):
    """JWKS local, faux storage, réseau fermé."""

    def setUp(self) -> None:
        super().setUp()
        self._patch(mock.patch.object(KeycloakJWTAuthentication, '_get_jwks_client',
                                      classmethod(lambda cls: LocalJwks())))
        self.storage = FakeStorage()
        self._patch(mock.patch.object(storage_client, 'requests', self.storage))
        self._patch(mock.patch.dict(storage_client._token_cache, {'value': '', 'exp': 0.0}))
        # Filet : tout appel HTTP non simulé échoue tout de suite, et se voit.
        self.network = self._patch(mock.patch(
            'requests.sessions.Session.request',
            side_effect=requests.ConnectionError('réseau fermé pendant les tests'),
        ))

    def _patch(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def as_(self, email: str) -> APIClient:
        return client_for(email)


class FamilyTreeCase(LabTestCase):
    """Un arbre privé d'Alice (Bob éditeur, Carol lectrice), garni d'un objet de
    chaque ressource, un arbre public d'Alice et l'arbre privé de Dave."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.tree = Tree.objects.create(name='Famille Martin', owner_email=ALICE)
        cls.public = Tree.objects.create(name='Arbre public', owner_email=ALICE, is_public=True)
        cls.other = Tree.objects.create(name='Arbre de Dave', owner_email=DAVE)
        cls.share_bob = TreeShare.objects.create(tree=cls.tree, email=BOB, role='EDITOR')
        cls.share_carol = TreeShare.objects.create(tree=cls.tree, email=CAROL, role='VIEWER')

        cls.objects = cls.populate(cls.tree)
        cls.public_objects = cls.populate(cls.public)
        cls.other_objects = cls.populate(cls.other)

    @staticmethod
    def populate(tree: Tree) -> dict:
        """Un objet par route du routeur, dans `tree`. Clé = préfixe de la route."""
        pere = Individual.objects.create(tree=tree, sex='M')
        mere = Individual.objects.create(tree=tree, sex='F')
        enfant = Individual.objects.create(tree=tree, sex='U')
        name = PersonalName.objects.create(individual=pere, givn='Jean', surn=f'Martin{tree.pk}',
                                           is_primary=True)
        PersonalName.objects.create(individual=mere, givn='Marie', surn='Durand', is_primary=True)
        PersonalName.objects.create(individual=enfant, givn='Paul', surn='Martin', is_primary=True)
        family = Family.objects.create(tree=tree)
        spouse = FamilySpouse.objects.create(family=family, individual=pere, role='HUSB')
        FamilySpouse.objects.create(family=family, individual=mere, role='WIFE')
        child = FamilyChild.objects.create(family=family, individual=enfant)
        place = Place.objects.create(tree=tree, name='Tours, Indre-et-Loire, France')
        event = Event.objects.create(tree=tree, individual=pere, tag='BIRT', place=place,
                                     **parse_date('1900').as_fields())
        media = MediaObject.objects.create(tree=tree, title=f'Portrait secret {tree.pk}',
                                           filename='portrait.jpg', mime='image/jpeg',
                                           storage_path='media/abc-portrait.jpg', size=4)
        link = MediaLink.objects.create(media=media, individual=pere, is_primary=True)
        repository = Repository.objects.create(tree=tree, name='Archives')
        source = Source.objects.create(tree=tree, title='Registre', repository=repository)
        citation = Citation.objects.create(source=source, individual=pere)
        note = SharedNote.objects.create(tree=tree, text='note')
        style = NodeStyle.objects.create(tree=tree, name='Style')
        rule = StyleRule.objects.create(tree=tree, name='Règle', style=style, condition={})
        edge = EdgeStyle.objects.create(tree=tree, name='Trait')
        template = CardTemplate.objects.create(tree=tree, name='Gabarit')
        node_layout = NodeLayout.objects.create(individual=pere, x=1, y=2)
        family_layout = FamilyLayout.objects.create(family=family, x=1, y=2)
        field = CustomFieldDef.objects.create(tree=tree, key='metier', label='Métier')
        match = EnrichmentMatch.objects.create(tree=tree, provider='wikitree', external_id='X-1')
        job = ImportJob.objects.create(tree=tree, filename='a.ged')
        famille2 = Family.objects.create(tree=tree)   # vide, sans position
        return {
            'individuals': pere, 'names': name, 'families': family,
            'family-spouses': spouse, 'family-children': child, 'events': event,
            'places': place, 'media': media, 'media-links': link, 'sources': source,
            'repositories': repository, 'citations': citation, 'shared-notes': note,
            'node-styles': style, 'style-rules': rule, 'edge-styles': edge,
            'card-templates': template, 'node-layouts': node_layout,
            'family-layouts': family_layout, 'custom-fields': field,
            'enrichment-matches': match, 'import-jobs': job,
            # hors routeur, pour les tests qui en ont besoin
            '_mere': mere, '_enfant': enfant, '_famille2': famille2,
        }

    def setUp(self) -> None:
        super().setUp()
        # Les octets des médias du jeu de données existent dans le faux storage.
        for tree, objs in ((self.tree, self.objects), (self.public, self.public_objects),
                           (self.other, self.other_objects)):
            self.storage.files.setdefault(ns(tree), {})[objs['media'].storage_path] = b'JPEG'
        self.alice, self.bob = self.as_(ALICE), self.as_(BOB)
        self.carol, self.dave = self.as_(CAROL), self.as_(DAVE)
