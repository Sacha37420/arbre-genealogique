"""
Fichier des personnes décédées de l'INSEE — la seule base nominative française
librement exploitable.

~27 millions de décès survenus en France depuis 1970, publiés en open data sur
data.gouv.fr et exposés en API REST par matchID (Fabrique numérique des
ministères sociaux). Aucune clé, aucun compte.

Ce que la source donne, et qu'aucun arbre collaboratif ne donnera : de l'état
civil **officiel**, pas de la saisie de bénévole. Date et commune de naissance
exactes, date et commune de décès, avec les coordonnées géographiques.

Ce qu'elle ne donne pas : **la filiation**. C'est un fichier d'identification, pas
un arbre — l'acte de décès nomme les parents, le fichier qui en dérive, non. D'où
`supports_relatives = False`, et le lien vers les Archives départementales que
chaque résultat embarque : la date et la commune de naissance sont exactement la
coordonnée qu'il faut pour ouvrir le bon registre et y lire l'acte de naissance,
lequel nomme les deux parents.

Limites à garder en tête :
  · uniquement les personnes **décédées**, et **depuis 1970** ;
  · uniquement les décès survenus **en France** (les Français morts à l'étranger
    n'y figurent qu'en partie) ;
  · les communes changent de nom et de code au fil des fusions : l'API renvoie
    l'historique, on garde le libellé le plus récent.
"""
from __future__ import annotations

from .base import PersonResult, Provider, ProviderError, SearchQuery, score_match

BASE = 'https://deces.matchid.io/deces/api/v1'

#: Portail des Archives départementales, par code de département. Les URL ne
#: suivent aucun schéma commun d'un département à l'autre : il n'y a pas de règle
#: à déduire, seulement un annuaire. FranceArchives sert de repli.
FRANCE_ARCHIVES = 'https://francearchives.gouv.fr/fr/annuaire/services'


def _date(value: str) -> str:
    """« 18930512 » → « 1893-05-12 ». Les jours et mois inconnus valent « 00 »."""
    raw = str(value or '').strip()
    if len(raw) != 8 or not raw.isdigit():
        return raw

    year, month, day = raw[:4], raw[4:6], raw[6:]
    if month == '00':
        return year
    if day == '00':
        return f'{year}-{month}'
    return f'{year}-{month}-{day}'


def _place(location: dict) -> str:
    """
    Libellé lisible d'un lieu : « Le Fuilet (49) ».

    `city` est une liste, parce qu'une commune peut avoir changé de nom ou avoir
    fusionné : l'API renvoie tous ses libellés connus. Le premier est celui de
    l'acte, c'est celui qu'attend l'utilisateur qui va chercher le registre.
    """
    if not location:
        return ''

    cities = location.get('city') or []
    city = cities[0] if cities else ''
    department = location.get('departmentCode') or ''
    country = location.get('country') or ''

    if city and department:
        return f'{city} ({department})'
    return city or country or ''


def _to_result(person: dict, query: SearchQuery | None = None) -> PersonResult:
    name = person.get('name') or {}
    birth = person.get('birth') or {}
    death = person.get('death') or {}
    birth_location = birth.get('location') or {}

    given = ' '.join(name.get('first') or [])
    surname = name.get('last') or ''
    department = birth_location.get('departmentCode') or ''

    result = PersonResult(
        provider=DecesInseeProvider.key,
        external_id=str(person.get('id') or ''),
        url=f'{BASE}/id/{person.get("id")}' if person.get('id') else '',
        given_name=given,
        surname=surname,
        sex=person.get('sex') or 'U',
        birth_date=_date(birth.get('date')),
        birth_place=_place(birth_location),
        death_date=_date(death.get('date')),
        death_place=_place(death.get('location') or {}),
        description=_describe(birth_location, death, department),
        raw=person,
    )
    if query is not None:
        result.score = score_match(result, query)
    return result


def _describe(birth_location: dict, death: dict, department: str) -> str:
    """Phrase affichée sous le candidat : ce qu'il apporte, et où lire la suite."""
    parts = []
    if age := death.get('age'):
        parts.append(f'Décédé(e) à {age} ans')

    cities = birth_location.get('city') or []
    if cities and department:
        parts.append(
            f'Acte de naissance à chercher dans les registres de {cities[0]}, '
            f'archives du département {department} — il nomme les deux parents',
        )
    return ' · '.join(parts)


class DecesInseeProvider(Provider):
    key = 'deces-insee'
    label = 'Fichier des décès (INSEE)'
    homepage = 'https://deces.matchid.io'
    docs_url = 'https://www.data.gouv.fr/datasets/fichier-des-personnes-decedees'
    required_credentials = []
    credential_help = 'Aucune clé : données publiques (open data, data.gouv.fr).'
    supports_search = True
    supports_fetch = True
    #: Un fichier d'identification, pas un arbre : aucune filiation à en tirer.
    supports_relatives = False
    coverage = (
        'État civil officiel : ~27 M de personnes décédées en France depuis 1970. '
        'Donne la date et la commune de naissance exactes — donc le registre où lire '
        'l’acte de naissance, qui nomme les parents. Ne contient aucun lien de parenté.'
    )

    def search(self, query: SearchQuery) -> list[PersonResult]:
        params: dict[str, str | int] = {'size': min(query.limit, 20)}

        # La recherche par champs est bien plus précise que le plein texte, mais elle
        # exige au moins un nom : sans lui, l'API renverrait tout le fichier.
        if query.surname:
            params['lastName'] = query.surname
        if query.given_name:
            params['firstName'] = query.given_name
        if query.birth_year:
            params['birthDate'] = str(query.birth_year)
        if query.death_year:
            params['deathDate'] = str(query.death_year)
        if query.place:
            params['birthCity'] = query.place

        if not query.surname and not query.given_name:
            if not query.text:
                raise ProviderError(
                    f'{self.label} : indiquez au moins un nom ou un prénom.', status=400,
                )
            params = {'q': query.text, 'size': min(query.limit, 20)}

        payload = self._get(f'{BASE}/search', params=params)
        persons = ((payload or {}).get('response') or {}).get('persons') or []

        results = [_to_result(person, query) for person in persons]
        return sorted(results, key=lambda r: r.score, reverse=True)

    def fetch(self, external_id: str) -> PersonResult:
        payload = self._get(f'{BASE}/id/{external_id}')
        persons = ((payload or {}).get('response') or {}).get('persons') or []
        if not persons:
            raise ProviderError(
                f'{self.label} : aucune personne pour l’identifiant « {external_id} ».',
                status=404,
            )
        return _to_result(persons[0])
