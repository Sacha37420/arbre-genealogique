"""
Geni (groupe MyHeritage) — arbre collaboratif « World Family Tree ».

Clé attendue : `access_token` OAuth 2.0, délivré aux applications enregistrées sur
https://www.geni.com/platform/developer/. Le jeton est passé en paramètre de requête,
comme le veut cette API.
"""
from .base import PersonResult, Provider, ProviderError, Relative, SearchQuery, score_match, split_name

BASE = 'https://www.geni.com/api'

SEX = {'male': 'M', 'female': 'F'}


class GeniProvider(Provider):
    key = 'geni'
    label = 'Geni'
    homepage = 'https://www.geni.com'
    docs_url = 'https://www.geni.com/platform/developer/help/api'
    required_credentials = ['access_token']
    credential_help = (
        'Enregistrez une application sur geni.com/platform/developer, puis collez '
        'le jeton OAuth 2.0 dans « access_token ».'
    )
    supports_relatives = True
    coverage = 'Arbre collaboratif mondial (World Family Tree), fort sur les lignées européennes.'

    def _params(self, **extra) -> dict:
        return {'access_token': self.credentials['access_token'], **extra}

    def search(self, query: SearchQuery) -> list[PersonResult]:
        if not query.full_name:
            raise ProviderError('Geni : précisez un nom à rechercher.', status=400)

        payload = self._get(f'{BASE}/profile/search', params=self._params(
            names=query.full_name, fields='id,name,first_name,last_name,gender,birth,death,mugshot_urls',
        ))

        results = []
        for profile in (payload.get('results') or [])[: query.limit]:
            result = _to_result(profile)
            result.score = score_match(result, query)
            results.append(result)
        return sorted(results, key=lambda r: r.score, reverse=True)

    def fetch(self, external_id: str) -> PersonResult:
        payload = self._get(f'{BASE}/{external_id}', params=self._params())
        if not payload or payload.get('error'):
            raise ProviderError(f'Geni : profil « {external_id} » introuvable.', status=404)

        result = _to_result(payload)
        result.relatives = self.relatives(external_id)
        return result

    def relatives(self, external_id: str) -> list[Relative]:
        payload = self._get(f'{BASE}/{external_id}/immediate-family', params=self._params())
        nodes = payload.get('nodes') or {}

        # Geni décrit la parenté dans « edges » : chaque nœud union relie ses membres
        # avec un rôle (child / partner). Les profils sont les autres nœuds.
        relatives: list[Relative] = []
        for node_id, node in nodes.items():
            if not node_id.startswith('profile-') or node_id == external_id:
                continue
            edges = node.get('edges') or {}
            relation = 'SIBLING'
            for edge in edges.values():
                rel = (edge or {}).get('rel', '')
                if rel == 'partner':
                    relation = 'SPOUSE'
                elif rel == 'child':
                    relation = 'CHILD'
                break

            result = _to_result(node)
            if relation == 'CHILD' and node.get('generation', 0) < 0:
                relation = {'M': 'FATHER', 'F': 'MOTHER'}.get(result.sex, 'FATHER')

            relatives.append(Relative(
                relation=relation,
                external_id=node_id,
                name=result.full_name,
                sex=result.sex,
                birth_date=result.birth_date,
                death_date=result.death_date,
            ))
        return relatives


def _to_result(profile: dict) -> PersonResult:
    pid = profile.get('id') or profile.get('guid') or ''
    given = profile.get('first_name') or ''
    surname = profile.get('last_name') or ''
    if not given and not surname:
        given, surname = split_name(profile.get('name', ''))

    mugshots = profile.get('mugshot_urls') or {}

    return PersonResult(
        provider='geni',
        external_id=str(pid),
        url=profile.get('profile_url') or (f'https://www.geni.com/people/{pid}' if pid else ''),
        given_name=given,
        surname=surname,
        sex=SEX.get(profile.get('gender', ''), 'U'),
        birth_date=_event_date(profile.get('birth')),
        birth_place=_event_place(profile.get('birth')),
        death_date=_event_date(profile.get('death')),
        death_place=_event_place(profile.get('death')),
        photo_url=mugshots.get('medium') or mugshots.get('large') or '',
        raw={'id': pid},
    )


def _event_date(event: dict | None) -> str:
    date = (event or {}).get('date') or {}
    parts = [str(date[k]) for k in ('day', 'month', 'year') if date.get(k)]
    return '/'.join(parts) if parts else ''


def _event_place(event: dict | None) -> str:
    location = (event or {}).get('location') or {}
    return location.get('place_name') or location.get('city') or ''
