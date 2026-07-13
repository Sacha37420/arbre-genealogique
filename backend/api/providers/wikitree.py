"""
WikiTree — arbre mondial collaboratif, libre d'accès.

Aucune clé n'est nécessaire pour les profils publics : c'est le fournisseur qui
fonctionne immédiatement, sans inscription. L'authentification n'est requise que
pour les profils privés, hors de portée d'une application tierce.

API : https://github.com/wikitree/wikitree-api  ·  base https://api.wikitree.com/api.php
Actions utilisées : searchPerson, getProfile, getRelatives.
"""
from .base import PersonResult, Provider, Relative, SearchQuery, score_match

BASE = 'https://api.wikitree.com/api.php'

FIELDS = (
    'Id,Name,FirstName,MiddleName,LastNameAtBirth,LastNameCurrent,Nicknames,'
    'BirthDate,DeathDate,BirthLocation,DeathLocation,Gender,PhotoData,IsLiving'
)

SEX = {'Male': 'M', 'Female': 'F'}


def _profile_to_result(profile: dict) -> PersonResult:
    photo = profile.get('PhotoData') or {}
    wikitree_id = profile.get('Name') or str(profile.get('Id', ''))

    return PersonResult(
        provider='wikitree',
        external_id=wikitree_id,
        url=f'https://www.wikitree.com/wiki/{wikitree_id}' if wikitree_id else '',
        given_name=' '.join(
            p for p in (profile.get('FirstName'), profile.get('MiddleName')) if p
        ).strip(),
        surname=profile.get('LastNameAtBirth') or profile.get('LastNameCurrent') or '',
        sex=SEX.get(profile.get('Gender', ''), 'U'),
        birth_date=_clean_date(profile.get('BirthDate')),
        birth_place=profile.get('BirthLocation') or '',
        death_date=_clean_date(profile.get('DeathDate')),
        death_place=profile.get('DeathLocation') or '',
        # WikiTree renvoie une URL relative pour les vignettes.
        photo_url=(
            f"https://www.wikitree.com{photo['url']}"
            if photo.get('url', '').startswith('/') else photo.get('url', '')
        ),
        raw=profile,
    )


def _clean_date(value: str | None) -> str:
    """WikiTree code les dates inconnues « 0000-00-00 » et les mois flous « 1875-00-00 »."""
    if not value or value.startswith('0000'):
        return ''
    year, month, day = (value.split('-') + ['00', '00'])[:3]
    if month == '00':
        return year
    if day == '00':
        return f'{year}-{month}'
    return value


class WikiTreeProvider(Provider):
    key = 'wikitree'
    label = 'WikiTree'
    homepage = 'https://www.wikitree.com'
    docs_url = 'https://github.com/wikitree/wikitree-api'
    required_credentials = []
    credential_help = 'Aucune clé : les profils publics sont librement interrogeables.'
    supports_relatives = True
    coverage = 'Arbre mondial collaboratif, ~40 M de profils, forte couverture Europe/Amérique du Nord.'

    def search(self, query: SearchQuery) -> list[PersonResult]:
        params = {
            'action': 'searchPerson',
            'fields': FIELDS,
            'limit': query.limit,
            'format': 'json',
        }
        if query.given_name:
            params['FirstName'] = query.given_name
        if query.surname:
            params['LastName'] = query.surname
        if query.birth_year:
            params['BirthDate'] = str(query.birth_year)
        if query.death_year:
            params['DeathDate'] = str(query.death_year)

        payload = self._get(BASE, params=params)
        matches = _unwrap(payload, 'matches')

        results = []
        for match in matches[: query.limit]:
            result = _profile_to_result(match)
            result.score = score_match(result, query)
            results.append(result)
        return sorted(results, key=lambda r: r.score, reverse=True)

    def fetch(self, external_id: str) -> PersonResult:
        payload = self._get(BASE, params={
            'action': 'getProfile', 'key': external_id, 'fields': FIELDS, 'format': 'json',
        })
        profile = _unwrap(payload, 'profile')
        if not profile:
            raise_not_found(external_id)
        result = _profile_to_result(profile)
        result.relatives = self.relatives(external_id)
        return result

    def relatives(self, external_id: str) -> list[Relative]:
        payload = self._get(BASE, params={
            'action': 'getRelatives',
            'keys': external_id,
            'getParents': 1,
            'getSpouses': 1,
            'getChildren': 1,
            'getSiblings': 1,
            'fields': FIELDS,
            'format': 'json',
        })

        items = _unwrap(payload, 'items') or []
        person = items[0].get('person', {}) if items else {}

        relatives: list[Relative] = []
        groups = (
            ('Parents', None),        # le sexe départage père et mère
            ('Spouses', 'SPOUSE'),
            ('Children', 'CHILD'),
            ('Siblings', 'SIBLING'),
        )
        for group, relation in groups:
            # WikiTree renvoie un dict indexé par Id, ou une liste vide s'il n'y a personne.
            entries = person.get(group) or {}
            values = entries.values() if isinstance(entries, dict) else entries
            for entry in values:
                res = _profile_to_result(entry)
                kind = relation
                if kind is None:
                    kind = {'M': 'FATHER', 'F': 'MOTHER'}.get(res.sex, 'FATHER')
                relatives.append(Relative(
                    relation=kind,
                    external_id=res.external_id,
                    name=res.full_name,
                    sex=res.sex,
                    birth_date=res.birth_date,
                    death_date=res.death_date,
                ))
        return relatives


def _unwrap(payload, key: str):
    """
    L'API WikiTree répond par une liste d'un élément : [{"status": 0, "<key>": …}].
    Certaines actions renvoient directement l'objet — on accepte les deux formes.
    """
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return [] if key in ('matches', 'items') else {}
    return payload.get(key) or ([] if key in ('matches', 'items') else {})


def raise_not_found(external_id: str):
    from .base import ProviderError
    raise ProviderError(f'WikiTree : profil « {external_id} » introuvable.', status=404)
