"""
FamilySearch — le plus gros fonds d'actes numérisés au monde (registres paroissiaux,
état civil, recensements), gratuit mais authentifié.

Clé attendue : `access_token` — un jeton OAuth 2.0 obtenu avec l'App Key délivrée
sur https://www.familysearch.org/developers/. Le jeton est fourni **par requête**,
jamais conservé côté serveur.

Format de réponse : GEDCOM X (https://www.familysearch.org/developers/docs/api/).
"""
from .base import PersonResult, Provider, ProviderError, Relative, SearchQuery, score_match

BASE = 'https://api.familysearch.org'

GEDCOMX_JSON = 'application/x-gedcomx-v1+json'
ATOM_JSON = 'application/x-gedcomx-atom+json'

SEX = {
    'http://gedcomx.org/Male': 'M',
    'http://gedcomx.org/Female': 'F',
}

FACT_BIRTH = 'http://gedcomx.org/Birth'
FACT_DEATH = 'http://gedcomx.org/Death'
FACT_OCCUPATION = 'http://gedcomx.org/Occupation'


class FamilySearchProvider(Provider):
    key = 'familysearch'
    label = 'FamilySearch'
    homepage = 'https://www.familysearch.org'
    docs_url = 'https://www.familysearch.org/developers/docs/api/resources'
    required_credentials = ['access_token']
    credential_help = (
        'Créez une application sur developers.familysearch.org pour obtenir une App Key, '
        'puis un jeton OAuth 2.0. Collez le jeton d’accès dans le champ « access_token ».'
    )
    supports_relatives = True
    coverage = ('Milliards d’actes indexés (état civil, registres paroissiaux, recensements) '
                'et arbre collaboratif mondial. Gratuit, compte requis.')

    @property
    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.credentials["access_token"]}',
            'Accept': GEDCOMX_JSON,
        }

    def search(self, query: SearchQuery) -> list[PersonResult]:
        # La syntaxe de recherche FamilySearch est un « q » à clés :
        #   q=givenName:Jean surname:Dupont birthLikeDate:1875
        terms = []
        if query.given_name:
            terms.append(f'givenName:"{query.given_name}"')
        if query.surname:
            terms.append(f'surname:"{query.surname}"')
        if query.birth_year:
            terms.append(f'birthLikeDate:{query.birth_year}')
        if query.death_year:
            terms.append(f'deathLikeDate:{query.death_year}')
        if query.place:
            terms.append(f'birthLikePlace:"{query.place}"')
        if not terms and query.text:
            terms.append(query.text)
        if not terms:
            raise ProviderError('FamilySearch : précisez au moins un nom.', status=400)

        payload = self._get(
            f'{BASE}/platform/tree/search',
            params={'q': ' '.join(terms), 'count': query.limit},
            headers={**self._headers, 'Accept': ATOM_JSON},
        )

        results = []
        for entry in (payload.get('entries') or [])[: query.limit]:
            content = ((entry.get('content') or {}).get('gedcomx') or {})
            for person in content.get('persons', []):
                result = _person_to_result(person)
                # FamilySearch fournit son propre score de pertinence (0–100).
                result.score = round(entry.get('score', 0) / 100, 3) or score_match(result, query)
                results.append(result)
                break
        return results

    def fetch(self, external_id: str) -> PersonResult:
        payload = self._get(
            f'{BASE}/platform/tree/persons/{external_id}', headers=self._headers,
        )
        persons = payload.get('persons') or []
        if not persons:
            raise ProviderError(f'FamilySearch : personne « {external_id} » introuvable.', status=404)

        result = _person_to_result(persons[0])
        result.relatives = self.relatives(external_id)
        return result

    def relatives(self, external_id: str) -> list[Relative]:
        """
        L'ascendance et la descendance sont deux ressources distinctes ; on ne
        demande qu'une génération de chaque, le reste se charge à la demande.
        """
        relatives: list[Relative] = []

        ancestry = self._get(
            f'{BASE}/platform/tree/ancestry',
            params={'person': external_id, 'generations': 2},
            headers=self._headers,
        )
        for person in ancestry.get('persons', []):
            number = (person.get('display') or {}).get('ascendancyNumber')
            # Numérotation Sosa-Stradonitz : 1 = la personne, 2 = père, 3 = mère.
            if number == '2':
                relatives.append(_to_relative(person, 'FATHER'))
            elif number == '3':
                relatives.append(_to_relative(person, 'MOTHER'))

        descendancy = self._get(
            f'{BASE}/platform/tree/descendancy',
            params={'person': external_id, 'generations': 2},
            headers=self._headers,
        )
        for person in descendancy.get('persons', []):
            number = (person.get('display') or {}).get('descendancyNumber', '')
            # « 1.1 », « 1.2 » = enfants directs ; « 1-S1 » = conjoint.
            if number.startswith('1.') and number.count('.') == 1:
                relatives.append(_to_relative(person, 'CHILD'))
            elif '-S' in number and number.split('-')[0] == '1':
                relatives.append(_to_relative(person, 'SPOUSE'))

        return relatives


def _person_to_result(person: dict) -> PersonResult:
    display = person.get('display') or {}
    pid = person.get('id', '')
    facts = {f.get('type'): f for f in (person.get('facts') or [])}

    given, surname = _names(person, display)

    return PersonResult(
        provider='familysearch',
        external_id=pid,
        url=f'https://www.familysearch.org/tree/person/details/{pid}' if pid else '',
        given_name=given,
        surname=surname,
        sex=SEX.get((person.get('gender') or {}).get('type', ''), 'U'),
        birth_date=display.get('birthDate', '') or _fact_date(facts.get(FACT_BIRTH)),
        birth_place=display.get('birthPlace', '') or _fact_place(facts.get(FACT_BIRTH)),
        death_date=display.get('deathDate', '') or _fact_date(facts.get(FACT_DEATH)),
        death_place=display.get('deathPlace', '') or _fact_place(facts.get(FACT_DEATH)),
        occupation=_fact_value(facts.get(FACT_OCCUPATION)),
        description=display.get('lifespan', ''),
        raw={'id': pid},
    )


def _names(person: dict, display: dict) -> tuple[str, str]:
    given = display.get('name', '')
    surname = ''
    for name in person.get('names', []):
        for form in name.get('nameForms', []):
            parts = {p.get('type'): p.get('value', '') for p in form.get('parts', [])}
            given = parts.get('http://gedcomx.org/Given', given)
            surname = parts.get('http://gedcomx.org/Surname', surname)
            if given or surname:
                return given, surname
    # Rien de structuré : on retombe sur le nom affiché, sans le découper à l'aveugle.
    return given, surname


def _fact_date(fact: dict | None) -> str:
    return ((fact or {}).get('date') or {}).get('original', '')


def _fact_place(fact: dict | None) -> str:
    return ((fact or {}).get('place') or {}).get('original', '')


def _fact_value(fact: dict | None) -> str:
    return (fact or {}).get('value', '')


def _to_relative(person: dict, relation: str) -> Relative:
    result = _person_to_result(person)
    return Relative(
        relation=relation,
        external_id=result.external_id,
        name=result.full_name,
        sex=result.sex,
        birth_date=result.birth_date,
        death_date=result.death_date,
    )
