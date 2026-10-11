"""Verrou 2 — la serrure de l'API, avec de vrais jetons signés.

arbre-genealogique n'a AUCUN groupe requis : tout compte du realm entre. Ce qui
tient la porte est donc (1) la signature et l'expiration, (2) ``azp`` — le jeton
doit avoir été émis pour CETTE application, sinon un jeton ``admin-cli`` (client
public à mot de passe du realm) suffirait — et (3) le claim ``email``, pivot de
toute l'autorisation par arbre.
"""
import jwt
from django.test import override_settings
from rest_framework.test import APIClient

from api import urls as api_urls

from .helpers import (
    ALICE,
    DAVE,
    FOREIGN_KEY,
    FamilyTreeCase,
    LabTestCase,
    client_for,
    make_token,
)

REFUS = (401, 403)


def bearer(token: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


class SerrureTests(LabTestCase):

    def test_sans_jeton_rien_ne_repond(self):
        """Inventaire tiré du routeur : aucune route n'est ouverte à un anonyme."""
        anonyme = APIClient()
        routes = [f'/api/{prefix}/' for prefix, _, _ in api_urls.router.registry]
        routes += ['/api/me/', '/api/enrich/providers/']
        self.assertGreaterEqual(len(routes), 26)
        for route in routes:
            with self.subTest(route=route):
                self.assertIn(anonyme.get(route).status_code, REFUS)
        for route in ('/api/enrich/search/', '/api/enrich/fetch/', '/api/enrich/import/',
                      '/api/trees/'):
            with self.subTest(route=route, method='POST'):
                self.assertIn(anonyme.post(route, {}, format='json').status_code, REFUS)

    def test_aucune_vue_ne_desactive_la_permission(self):
        """Pas d'endpoint AllowAny dans cette app : si une vue en gagne un, ce
        test oblige à l'écrire ici et à dire ce qu'il laisse passer."""
        from rest_framework.permissions import AllowAny
        vues = [viewset for _, viewset, _ in api_urls.router.registry]
        vues += [p.callback.view_class for p in api_urls.urlpatterns if hasattr(p, 'callback')
                 and hasattr(p.callback, 'view_class')]
        self.assertGreaterEqual(len(vues), 29)
        for vue in vues:
            with self.subTest(vue=vue.__name__):
                self.assertNotIn(AllowAny, vue.permission_classes)

    def test_jeton_valide_sans_groupe_est_accepte(self):
        # Voulu : l'app est ouverte à tout compte du realm (aucun --require-group).
        resp = bearer(make_token(DAVE)).get('/api/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['email'], DAVE)
        self.assertEqual(resp.json()['tree_count'], 0)

    def test_jeton_d_un_autre_client_est_refuse(self):
        for azp in ('admin-cli', 'account', 'storage', 'arbre-genealogique-admin', '', None):
            with self.subTest(azp=azp):
                resp = bearer(make_token(ALICE, azp=azp)).get('/api/me/')
                self.assertIn(resp.status_code, REFUS)
                self.assertIn('autre client', resp.json()['detail'])

    def test_jeton_expire_est_refuse(self):
        resp = bearer(make_token(ALICE, exp_in=-30)).get('/api/me/')
        self.assertIn(resp.status_code, REFUS)
        self.assertIn('Token invalide', resp.json()['detail'])

    def test_jeton_signe_par_une_autre_cle_est_refuse(self):
        resp = bearer(make_token(ALICE, key=FOREIGN_KEY)).get('/api/me/')
        self.assertIn(resp.status_code, REFUS)
        self.assertIn('Token invalide', resp.json()['detail'])

    def test_jeton_non_signe_ou_hs256_est_refuse(self):
        claims = {'azp': 'arbre-genealogique', 'email': ALICE, 'exp': 9_999_999_999}
        sans_signature = jwt.encode(claims, None, algorithm='none')
        hs256 = jwt.encode(claims, 'x' * 32, algorithm='HS256')
        for nom, tok in (('none', sans_signature), ('HS256', hs256), ('bruit', 'pas.un.jeton')):
            with self.subTest(jeton=nom):
                self.assertIn(bearer(tok).get('/api/me/').status_code, REFUS)

    def test_jeton_sans_email_est_refuse(self):
        # L'e-mail est la clé de toute l'autorisation par arbre : sans lui, un
        # jeton vaudrait pour l'utilisateur « '' ».
        resp = bearer(make_token(None)).get('/api/me/')
        self.assertIn(resp.status_code, REFUS)
        self.assertIn('email', resp.json()['detail'])

    def test_un_schema_autre_que_bearer_n_authentifie_pas(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {make_token(ALICE)}')
        self.assertIn(client.get('/api/me/').status_code, REFUS)

    @override_settings(KEYCLOAK_REQUIRED_GROUPS='famille, amis')
    def test_si_un_groupe_devient_requis_il_est_applique(self):
        # L'app n'en a pas aujourd'hui, mais le jour où `--require-group` est
        # posé, c'est ce code qui doit fermer la porte.
        for groups in (None, [], ['developers']):
            with self.subTest(groups=groups):
                resp = bearer(make_token(ALICE, groups=groups)).get('/api/me/')
                self.assertIn(resp.status_code, REFUS)
                self.assertIn('groupe(s)', resp.json()['detail'])
        self.assertEqual(bearer(make_token(ALICE, groups=['amis'])).get('/api/me/').status_code, 200)

    def test_me_ne_fait_confiance_qu_au_jeton(self):
        client = client_for(ALICE, username='alice')
        resp = client.get('/api/me/?email=bob@example.org')
        self.assertEqual(resp.json()['email'], ALICE)
        self.assertTrue(resp.json()['is_new'])
        self.assertFalse(client.get('/api/me/').json()['is_new'])


class JetonEnParametreTests(FamilyTreeCase):
    """« ?token= » : accepté sur le seul contenu d'un média (balise <img>)."""

    def setUp(self):
        super().setUp()
        self.url = f"/api/media/{self.objects['media'].pk}/file/"

    def get(self, token: str):
        return APIClient().get(self.url, {'token': token})

    def test_jeton_valide_en_parametre_sert_le_fichier(self):
        resp = self.get(make_token(ALICE))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'JPEG')

    def test_sans_jeton_du_tout(self):
        self.assertIn(APIClient().get(self.url).status_code, REFUS)

    def test_jeton_expire_mal_signe_ou_sans_email_en_parametre(self):
        for nom, tok in (('expiré', make_token(ALICE, exp_in=-30)),
                         ('autre clé', make_token(ALICE, key=FOREIGN_KEY)),
                         ('sans email', make_token(None)),
                         ('bruit', 'abc')):
            with self.subTest(jeton=nom):
                self.assertIn(self.get(tok).status_code, REFUS)
        self.assertEqual(self.storage.calls, [])

    def test_le_parametre_ne_donne_pas_plus_que_l_en_tete(self):
        # Dave n'a aucun droit sur l'arbre d'Alice : en-tête ou paramètre, 404.
        self.assertEqual(self.get(make_token(DAVE)).status_code, 404)
        self.assertEqual(self.storage.calls, [])

    def test_le_parametre_n_est_lu_nulle_part_ailleurs(self):
        tok = make_token(ALICE)
        for route in ('/api/me/', '/api/trees/', f'/api/media/{self.objects["media"].pk}/',
                      f'/api/trees/{self.tree.pk}/graph/'):
            with self.subTest(route=route):
                self.assertIn(APIClient().get(route, {'token': tok}).status_code, REFUS)

    def test_en_tete_d_un_autre_client_refuse_aussi_sur_le_fichier(self):
        resp = bearer(make_token(ALICE, azp='admin-cli')).get(self.url)
        self.assertIn(resp.status_code, REFUS)

    def test_jeton_d_un_autre_client_en_parametre_est_refuse(self):
        # Corrigé (D1) : « ?token= » passe par la même validation que l'en-tête.
        # Un jeton `admin-cli` (client public à mot de passe du realm) y était accepté.
        resp = self.get(make_token(ALICE, azp='admin-cli'))
        self.assertIn(resp.status_code, REFUS)
        self.assertIn('autre client', resp.json()['detail'])
        self.assertEqual(self.storage.calls, [])

    @override_settings(KEYCLOAK_REQUIRED_GROUPS='famille')
    def test_groupe_requis_applique_aussi_au_parametre(self):
        self.assertIn(self.get(make_token(ALICE, groups=['amis'])).status_code, REFUS)
        self.assertEqual(self.get(make_token(ALICE, groups=['famille'])).status_code, 200)
