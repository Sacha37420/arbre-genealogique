"""
Socle commun aux fournisseurs généalogiques externes.

Principe retenu : **les clés d'API ne sont jamais stockées côté serveur**. Elles
accompagnent chaque requête, envoyées par le client dans le corps (`credentials`)
ou dans l'en-tête `X-Provider-Key`. Le backend n'est qu'un relais : il traduit la
réponse hétérogène de chaque fournisseur en un PersonResult uniforme, ce qui
permet à l'interface de traiter WikiTree et FamilySearch avec le même code.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import ClassVar

import requests

TIMEOUT = 15
USER_AGENT = 'arbre-genealogique/1.0 (lab SSO; +https://github.com/Sacha37420/arbre-genealogique)'


class ProviderError(Exception):
    """Erreur exploitable par l'interface (clé absente, quota, service HS…)."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class SearchQuery:
    given_name: str = ''
    surname: str = ''
    birth_year: int | None = None
    death_year: int | None = None
    place: str = ''
    text: str = ''
    limit: int = 10

    @property
    def full_name(self) -> str:
        return ' '.join(p for p in (self.given_name, self.surname) if p).strip() or self.text


@dataclass
class Relative:
    """Un proche annoncé par le fournisseur, pas encore rattaché à notre arbre."""

    relation: str  # FATHER / MOTHER / SPOUSE / CHILD / SIBLING
    external_id: str = ''
    name: str = ''
    sex: str = 'U'
    birth_date: str = ''
    death_date: str = ''


@dataclass
class PersonResult:
    """Forme normalisée d'une personne, quelle que soit sa provenance."""

    provider: str
    external_id: str
    url: str = ''
    given_name: str = ''
    surname: str = ''
    sex: str = 'U'
    birth_date: str = ''
    birth_place: str = ''
    death_date: str = ''
    death_place: str = ''
    occupation: str = ''
    description: str = ''
    photo_url: str = ''
    score: float = 0.0
    relatives: list[Relative] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return ' '.join(p for p in (self.given_name, self.surname) if p).strip()

    def as_dict(self) -> dict:
        return asdict(self)


class Provider:
    """Contrat que chaque fournisseur implémente."""

    key: ClassVar[str]
    label: ClassVar[str]
    homepage: ClassVar[str] = ''
    docs_url: ClassVar[str] = ''
    #: Nom des clés attendues dans `credentials` ; vide = fournisseur libre d'accès.
    required_credentials: ClassVar[list[str]] = []
    #: Comment obtenir la clé — affiché tel quel dans l'interface.
    credential_help: ClassVar[str] = ''
    supports_search: ClassVar[bool] = True
    supports_fetch: ClassVar[bool] = True
    supports_relatives: ClassVar[bool] = False
    coverage: ClassVar[str] = ''

    def __init__(self, credentials: dict | None = None) -> None:
        self.credentials = credentials or {}
        missing = [k for k in self.required_credentials if not self.credentials.get(k)]
        if missing:
            raise ProviderError(
                f"{self.label} : clé d'API manquante ({', '.join(missing)}). "
                f"{self.credential_help}",
                status=400,
            )

    # ── HTTP ──────────────────────────────────────────────────────────────
    def _get(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> dict:
        try:
            response = requests.get(
                url,
                params=params,
                headers={'User-Agent': USER_AGENT, **(headers or {})},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ProviderError(f'{self.label} : service injoignable ({exc}).') from exc

        if response.status_code in (401, 403):
            raise ProviderError(
                f'{self.label} : clé d’API refusée ({response.status_code}). {self.credential_help}',
                status=401,
            )
        if response.status_code == 429:
            raise ProviderError(f'{self.label} : quota dépassé, réessayez plus tard.', status=429)
        if response.status_code >= 400:
            raise ProviderError(
                f'{self.label} : réponse {response.status_code} — {response.text[:200]}'
            )

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f'{self.label} : réponse illisible (JSON attendu).') from exc

    # ── À implémenter ─────────────────────────────────────────────────────
    def search(self, query: SearchQuery) -> list[PersonResult]:
        raise ProviderError(f'{self.label} : la recherche n’est pas disponible.', status=400)

    def fetch(self, external_id: str) -> PersonResult:
        raise ProviderError(f'{self.label} : la consultation par identifiant n’est pas disponible.',
                            status=400)

    def relatives(self, external_id: str) -> list[Relative]:
        return []

    # ── Description exposée à l'interface ─────────────────────────────────
    @classmethod
    def describe(cls) -> dict:
        return {
            'key': cls.key,
            'label': cls.label,
            'homepage': cls.homepage,
            'docs_url': cls.docs_url,
            'requires_key': bool(cls.required_credentials),
            'required_credentials': cls.required_credentials,
            'credential_help': cls.credential_help,
            'supports_search': cls.supports_search,
            'supports_fetch': cls.supports_fetch,
            'supports_relatives': cls.supports_relatives,
            'coverage': cls.coverage,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Score de correspondance — sert à trier les candidats
# ─────────────────────────────────────────────────────────────────────────────

def score_match(result: PersonResult, query: SearchQuery) -> float:
    """
    Note de 0 à 1 : le nom pèse le plus, les années affinent.

    Un écart d'un an sur une date ancienne est banal (calendriers, déclarations
    tardives) : on tolère ±2 ans avant de pénaliser.
    """
    score = 0.0

    if query.surname and result.surname:
        if query.surname.lower() == result.surname.lower():
            score += 0.45
        elif query.surname.lower() in result.surname.lower():
            score += 0.25

    if query.given_name and result.given_name:
        if query.given_name.lower() == result.given_name.lower():
            score += 0.3
        elif query.given_name.lower().split()[0] in result.given_name.lower():
            score += 0.15

    for wanted, found in ((query.birth_year, result.birth_date), (query.death_year, result.death_date)):
        if wanted and (year := extract_year(found)):
            gap = abs(year - wanted)
            if gap == 0:
                score += 0.125
            elif gap <= 2:
                score += 0.06

    if query.place and result.birth_place and query.place.lower() in result.birth_place.lower():
        score += 0.05

    return round(min(score, 1.0), 3)


def extract_year(value: str) -> int | None:
    """Extrait la première année plausible d'une date en texte libre."""
    if not value:
        return None
    m = re.search(r'\b(1[0-9]{3}|20[0-2][0-9])\b', str(value))
    return int(m.group(1)) if m else None


def split_name(full: str) -> tuple[str, str]:
    """« Jean Baptiste Dupont » → ("Jean Baptiste", "Dupont")."""
    parts = (full or '').strip().split()
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return ' '.join(parts[:-1]), parts[-1]
