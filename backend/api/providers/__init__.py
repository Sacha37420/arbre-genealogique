"""
Registre des fournisseurs externes.

Ajouter une source revient à écrire une classe Provider et à l'inscrire ici :
les endpoints /api/enrich/* la découvrent automatiquement, et l'interface affiche
son formulaire de clé sans changement côté front.
"""
from .base import PersonResult, Provider, ProviderError, Relative, SearchQuery
from .familysearch import FamilySearchProvider
from .geni import GeniProvider
from .geocoding import Geocoder
from .myheritage import MyHeritageProvider
from .wikidata import WikidataProvider
from .wikitree import WikiTreeProvider

PROVIDERS: dict[str, type[Provider]] = {
    provider.key: provider
    for provider in (
        WikiTreeProvider,      # sans clé
        WikidataProvider,      # sans clé
        FamilySearchProvider,  # access_token
        GeniProvider,          # access_token
        MyHeritageProvider,    # access_token
    )
}


def get_provider(key: str, credentials: dict | None = None) -> Provider:
    cls = PROVIDERS.get(key)
    if cls is None:
        known = ', '.join(PROVIDERS)
        raise ProviderError(f'Fournisseur « {key} » inconnu. Disponibles : {known}.', status=404)
    return cls(credentials)


def describe_all() -> list[dict]:
    return [cls.describe() for cls in PROVIDERS.values()] + [Geocoder.describe()]


__all__ = [
    'PROVIDERS', 'get_provider', 'describe_all', 'Geocoder',
    'Provider', 'ProviderError', 'PersonResult', 'Relative', 'SearchQuery',
]
