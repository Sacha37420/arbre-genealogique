"""
Géocodage des lieux — pour situer naissances, décès et migrations sur une carte.

Deux fournisseurs :
  · Nominatim (OpenStreetMap) — sans clé, mais limité à ~1 req/s ; suffisant pour
    géocoder les lieux d'un arbre au fil de la saisie.
  · Geoapify — avec clé, quota confortable, préférable pour géocoder un arbre entier
    d'un coup.

Ce ne sont pas des Provider au sens généalogique (ils ne renvoient pas de personnes) :
ils exposent geocode(place) → coordonnées.
"""
import requests

from .base import ProviderError, TIMEOUT, USER_AGENT

NOMINATIM = 'https://nominatim.openstreetmap.org/search'
GEOAPIFY = 'https://api.geoapify.com/v1/geocode/search'


class Geocoder:
    key = 'nominatim'
    label = 'Nominatim (OpenStreetMap)'
    docs_url = 'https://nominatim.org/release-docs/latest/api/Search/'
    required_credentials = []
    credential_help = (
        'Aucune clé. Alternative : fournissez « geoapify_key » pour utiliser Geoapify, '
        'plus rapide et sans limite de débit stricte.'
    )
    coverage = 'Géocodage mondial des lieux (communes, paroisses, cimetières).'

    def __init__(self, credentials: dict | None = None) -> None:
        self.credentials = credentials or {}
        self.geoapify_key = self.credentials.get('geoapify_key', '')

    @classmethod
    def describe(cls) -> dict:
        return {
            'key': cls.key,
            'label': cls.label,
            'homepage': 'https://www.openstreetmap.org',
            'docs_url': cls.docs_url,
            'requires_key': False,
            'required_credentials': [],
            'optional_credentials': ['geoapify_key'],
            'credential_help': cls.credential_help,
            'supports_search': False,
            'supports_fetch': False,
            'supports_relatives': False,
            'supports_geocoding': True,
            'coverage': cls.coverage,
        }

    def geocode(self, place: str) -> dict | None:
        """Renvoie {'latitude', 'longitude', 'display_name', 'provider'} ou None."""
        if not place.strip():
            return None
        return self._geoapify(place) if self.geoapify_key else self._nominatim(place)

    def _nominatim(self, place: str) -> dict | None:
        data = self._call(NOMINATIM, {'q': place, 'format': 'json', 'limit': 1})
        if not data:
            return None
        hit = data[0]
        return {
            'latitude': float(hit['lat']),
            'longitude': float(hit['lon']),
            'display_name': hit.get('display_name', place),
            'provider': 'nominatim',
        }

    def _geoapify(self, place: str) -> dict | None:
        data = self._call(GEOAPIFY, {'text': place, 'limit': 1, 'apiKey': self.geoapify_key})
        features = (data or {}).get('features') or []
        if not features:
            return None
        props = features[0].get('properties', {})
        return {
            'latitude': props['lat'],
            'longitude': props['lon'],
            'display_name': props.get('formatted', place),
            'provider': 'geoapify',
        }

    def _call(self, url: str, params: dict):
        try:
            response = requests.get(
                url, params=params, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ProviderError(f'Géocodage injoignable ({exc}).') from exc

        if response.status_code in (401, 403):
            raise ProviderError('Géocodage : clé Geoapify refusée.', status=401)
        if response.status_code >= 400:
            raise ProviderError(f'Géocodage : réponse {response.status_code}.')

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError('Géocodage : réponse illisible.') from exc
