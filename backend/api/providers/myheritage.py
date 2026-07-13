"""
MyHeritage — Family Graph API.

Clé attendue : `access_token` OAuth 2.0 (https://www.familygraph.com/documentation).

Limite assumée : la recherche plein texte dans le catalogue MyHeritage n'est pas
exposée publiquement par Family Graph — l'API permet de **lire** les objets d'un
arbre auquel le jeton donne accès (individus, sites, photos), pas d'interroger le
moteur de recherche du site. On expose donc la lecture par identifiant, et la
recherche est déclarée indisponible plutôt que simulée.
"""
from .base import PersonResult, Provider, ProviderError, Relative, SearchQuery, split_name

BASE = 'https://familygraph.myheritage.com'

SEX = {'male': 'M', 'female': 'F'}


class MyHeritageProvider(Provider):
    key = 'myheritage'
    label = 'MyHeritage (Family Graph)'
    homepage = 'https://www.myheritage.fr'
    docs_url = 'https://www.familygraph.com/documentation'
    required_credentials = ['access_token']
    credential_help = (
        'Enregistrez une application sur familygraph.com, obtenez un jeton OAuth 2.0 '
        'et collez-le dans « access_token ».'
    )
    supports_search = False
    supports_relatives = True
    coverage = ('Lecture des arbres MyHeritage auxquels le jeton donne accès '
                '(individus, photos, sources). La recherche du catalogue n’est pas exposée par l’API.')

    def _params(self, **extra) -> dict:
        return {'access_token': self.credentials['access_token'], 'format': 'json', **extra}

    def search(self, query: SearchQuery) -> list[PersonResult]:
        raise ProviderError(
            'MyHeritage : la Family Graph API ne permet pas de rechercher dans le catalogue. '
            'Utilisez l’identifiant d’un individu (ex. individual-123456-1) pour l’importer.',
            status=400,
        )

    def fetch(self, external_id: str) -> PersonResult:
        payload = self._get(f'{BASE}/{external_id}', params=self._params(
            fields='id,name,first_name,last_name,gender,birth_date,death_date,'
                   'birth_place,death_place,personal_photo,link',
        ))
        if not payload or payload.get('error'):
            raise ProviderError(f'MyHeritage : objet « {external_id} » introuvable.', status=404)

        result = _to_result(payload)
        result.relatives = self.relatives(external_id)
        return result

    def relatives(self, external_id: str) -> list[Relative]:
        relatives: list[Relative] = []
        # Family Graph expose les proches comme des « connections » de l'individu.
        for connection, relation in (
            ('parents', None), ('spouses', 'SPOUSE'),
            ('children', 'CHILD'), ('siblings', 'SIBLING'),
        ):
            try:
                payload = self._get(
                    f'{BASE}/{external_id}/{connection}',
                    params=self._params(fields='id,name,gender,birth_date,death_date'),
                )
            except ProviderError:
                # Toutes les connexions n'existent pas pour tous les objets : on
                # ignore celles que l'API refuse plutôt que d'échouer en bloc.
                continue

            for item in payload.get('data', []):
                result = _to_result(item)
                kind = relation or {'M': 'FATHER', 'F': 'MOTHER'}.get(result.sex, 'FATHER')
                relatives.append(Relative(
                    relation=kind,
                    external_id=result.external_id,
                    name=result.full_name,
                    sex=result.sex,
                    birth_date=result.birth_date,
                    death_date=result.death_date,
                ))
        return relatives


def _to_result(data: dict) -> PersonResult:
    given = data.get('first_name') or ''
    surname = data.get('last_name') or ''
    if not given and not surname:
        given, surname = split_name(data.get('name', ''))

    photo = data.get('personal_photo') or {}

    return PersonResult(
        provider='myheritage',
        external_id=str(data.get('id', '')),
        url=data.get('link', ''),
        given_name=given,
        surname=surname,
        sex=SEX.get(data.get('gender', ''), 'U'),
        birth_date=_date(data.get('birth_date')),
        birth_place=_place(data.get('birth_place')),
        death_date=_date(data.get('death_date')),
        death_place=_place(data.get('death_place')),
        photo_url=photo.get('thumbnails', [{}])[0].get('url', '') if photo.get('thumbnails') else '',
        raw={'id': data.get('id')},
    )


def _date(value) -> str:
    if isinstance(value, dict):
        return value.get('gedcom') or value.get('text') or ''
    return value or ''


def _place(value) -> str:
    if isinstance(value, dict):
        return value.get('name', '')
    return value or ''
