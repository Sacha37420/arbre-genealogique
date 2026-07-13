"""
Wikidata — base de connaissances libre, sans clé.

Intérêt en généalogie : les personnages publics (nobles, artistes, politiques) y
sont reliés par des propriétés de parenté explicites, et leurs portraits sont sur
Wikimedia Commons, réutilisables. C'est la meilleure source gratuite pour amorcer
une branche « célèbre » ou récupérer une photo libre de droits.

Deux appels : wbsearchentities pour les candidats, wbgetentities pour les faits.
"""
from .base import PersonResult, Provider, ProviderError, Relative, SearchQuery, score_match, split_name

API = 'https://www.wikidata.org/w/api.php'
COMMONS_FILE = 'https://commons.wikimedia.org/wiki/Special:FilePath/{}?width=500'

# Propriétés Wikidata
P_INSTANCE_OF = 'P31'
Q_HUMAN = 'Q5'
P_SEX = 'P21'
P_BIRTH = 'P569'
P_DEATH = 'P570'
P_BIRTH_PLACE = 'P19'
P_DEATH_PLACE = 'P20'
P_FATHER = 'P22'
P_MOTHER = 'P25'
P_SPOUSE = 'P26'
P_CHILD = 'P40'
P_SIBLING = 'P3373'
P_IMAGE = 'P18'
P_OCCUPATION = 'P106'

Q_MALE, Q_FEMALE = 'Q6581097', 'Q6581072'

RELATION_PROPS = {
    P_FATHER: 'FATHER',
    P_MOTHER: 'MOTHER',
    P_SPOUSE: 'SPOUSE',
    P_CHILD: 'CHILD',
    P_SIBLING: 'SIBLING',
}


class WikidataProvider(Provider):
    key = 'wikidata'
    label = 'Wikidata'
    homepage = 'https://www.wikidata.org'
    docs_url = 'https://www.wikidata.org/wiki/Wikidata:Data_access'
    required_credentials = []
    credential_help = 'Aucune clé nécessaire.'
    supports_relatives = True
    coverage = ('Personnalités publiques de toutes époques, liens de parenté structurés '
                'et portraits libres de droits (Wikimedia Commons).')

    def __init__(self, credentials: dict | None = None, language: str = 'fr') -> None:
        super().__init__(credentials)
        self.language = language

    def search(self, query: SearchQuery) -> list[PersonResult]:
        payload = self._get(API, params={
            'action': 'wbsearchentities',
            'search': query.full_name,
            'language': self.language,
            'uselang': self.language,
            'type': 'item',
            'limit': max(query.limit * 2, 10),  # on filtrera les non-humains
            'format': 'json',
        })
        candidates = [hit['id'] for hit in payload.get('search', [])]
        if not candidates:
            return []

        entities = self._entities(candidates)
        results = []
        for qid in candidates:
            entity = entities.get(qid)
            if not entity or not _is_human(entity):
                continue
            result = self._to_result(qid, entity)
            result.score = score_match(result, query)
            results.append(result)
            if len(results) >= query.limit:
                break
        return sorted(results, key=lambda r: r.score, reverse=True)

    def fetch(self, external_id: str) -> PersonResult:
        entities = self._entities([external_id])
        entity = entities.get(external_id)
        if not entity:
            raise ProviderError(f'Wikidata : élément « {external_id} » introuvable.', status=404)

        result = self._to_result(external_id, entity)
        # Les proches ne portent qu'un QID : un second appel donne leurs noms.
        related_ids = [
            r.external_id for r in _relatives(entity) if r.external_id
        ]
        labels = self._labels(related_ids) if related_ids else {}
        for relative in _relatives(entity):
            relative.name = labels.get(relative.external_id, relative.external_id)
            result.relatives.append(relative)
        return result

    def relatives(self, external_id: str) -> list[Relative]:
        return self.fetch(external_id).relatives

    # ── Appels bruts ──────────────────────────────────────────────────────
    def _entities(self, qids: list[str]) -> dict:
        payload = self._get(API, params={
            'action': 'wbgetentities',
            'ids': '|'.join(qids[:50]),  # limite de l'API
            'props': 'claims|labels|descriptions|sitelinks',
            'languages': f'{self.language}|en',
            'format': 'json',
        })
        return payload.get('entities', {})

    def _labels(self, qids: list[str]) -> dict[str, str]:
        payload = self._get(API, params={
            'action': 'wbgetentities',
            'ids': '|'.join(qids[:50]),
            'props': 'labels',
            'languages': f'{self.language}|en',
            'format': 'json',
        })
        labels = {}
        for qid, entity in payload.get('entities', {}).items():
            labels[qid] = _label(entity, self.language)
        return labels

    def _to_result(self, qid: str, entity: dict) -> PersonResult:
        label = _label(entity, self.language)
        given, surname = split_name(label)

        sex_qid = _entity_claim(entity, P_SEX)
        image = _string_claim(entity, P_IMAGE)

        return PersonResult(
            provider='wikidata',
            external_id=qid,
            url=f'https://www.wikidata.org/wiki/{qid}',
            given_name=given,
            surname=surname,
            sex={Q_MALE: 'M', Q_FEMALE: 'F'}.get(sex_qid, 'U'),
            birth_date=_time_claim(entity, P_BIRTH),
            death_date=_time_claim(entity, P_DEATH),
            description=_description(entity, self.language),
            photo_url=COMMONS_FILE.format(image.replace(' ', '_')) if image else '',
            raw={'qid': qid},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Lecture des claims Wikidata
# ─────────────────────────────────────────────────────────────────────────────

def _claims(entity: dict, prop: str) -> list[dict]:
    return (entity.get('claims') or {}).get(prop, [])


def _main(claim: dict):
    return ((claim.get('mainsnak') or {}).get('datavalue') or {}).get('value')


def _is_human(entity: dict) -> bool:
    return any(
        (_main(c) or {}).get('id') == Q_HUMAN for c in _claims(entity, P_INSTANCE_OF)
    )


def _entity_claim(entity: dict, prop: str) -> str:
    for claim in _claims(entity, prop):
        value = _main(claim)
        if isinstance(value, dict) and value.get('id'):
            return value['id']
    return ''


def _string_claim(entity: dict, prop: str) -> str:
    for claim in _claims(entity, prop):
        value = _main(claim)
        if isinstance(value, str):
            return value
    return ''


def _time_claim(entity: dict, prop: str) -> str:
    """
    Wikidata code le temps « +1769-08-15T00:00:00Z » avec une précision (9 = année,
    10 = mois, 11 = jour). On restitue une date lisible, jamais plus précise que
    ce que la source affirme.
    """
    for claim in _claims(entity, prop):
        value = _main(claim)
        if not isinstance(value, dict):
            continue
        time = value.get('time', '')
        precision = value.get('precision', 11)
        m = time.lstrip('+').split('T')[0]
        try:
            year, month, day = m.split('-')[:3]
        except ValueError:
            continue
        if precision <= 9:
            return year
        if precision == 10:
            return f'{year}-{month}'
        return f'{year}-{month}-{day}'
    return ''


def _label(entity: dict, language: str) -> str:
    labels = entity.get('labels') or {}
    for lang in (language, 'en'):
        if lang in labels:
            return labels[lang].get('value', '')
    return next((v.get('value', '') for v in labels.values()), '')


def _description(entity: dict, language: str) -> str:
    descriptions = entity.get('descriptions') or {}
    for lang in (language, 'en'):
        if lang in descriptions:
            return descriptions[lang].get('value', '')
    return ''


def _relatives(entity: dict) -> list[Relative]:
    relatives = []
    for prop, relation in RELATION_PROPS.items():
        for claim in _claims(entity, prop):
            value = _main(claim)
            if isinstance(value, dict) and value.get('id'):
                relatives.append(Relative(relation=relation, external_id=value['id']))
    return relatives
