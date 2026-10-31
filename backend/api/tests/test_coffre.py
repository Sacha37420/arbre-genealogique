"""Coffre des clés d'API des fournisseurs (I3) — `/api/provider-credentials/<fournisseur>/`.

Ce qui est tenu : la valeur n'est jamais rendue (ni extrait), elle est chiffrée
en base sous `VAULT_ENCRYPTION_KEY` et se relit ; chacun n'a que son coffre ;
l'appel au fournisseur reçoit la clé du coffre et ignore toute clé envoyée dans
la requête ; une chaîne vide oublie la clé.
"""
import logging
from unittest import mock

from cryptography.fernet import Fernet
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient

from api.models import ProviderCredential
from api import providers
from api.providers import PersonResult

from .helpers import ALICE, BOB, DAVE, FamilyTreeCase, LabTestCase, client_for

SECRET = 'cle-geni-tres-secrete-42'
URL = '/api/provider-credentials/geni/'


class _FauxFournisseur:
    def __init__(self):
        self.appels = []

    def search(self, query):
        self.appels.append(('search', query))
        return []

    def fetch(self, external_id):
        self.appels.append(('fetch', external_id))
        return PersonResult(provider='geni', external_id=external_id, given_name='Sam',
                            surname='Clemens')


class CoffreTests(LabTestCase):

    def setUp(self):
        super().setUp()
        self.alice = client_for(ALICE)
        self.bob = client_for(BOB)

    def deposer(self, client=None, value=SECRET):
        return (client or self.alice).put(URL, {'access_token': value}, format='json')

    def test_put_puis_get_ne_rend_jamais_la_valeur(self):
        self.assertEqual(self.deposer().status_code, 204)
        resp = self.alice.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'access_token_configured': True})
        self.assertNotIn(SECRET.encode(), resp.content)
        self.assertNotIn(SECRET[:8].encode(), resp.content)     # ni un extrait

    def test_get_sans_rien_depose(self):
        self.assertEqual(self.alice.get(URL).json(), {'access_token_configured': False})

    def test_en_base_la_valeur_est_chiffree_et_se_relit(self):
        self.deposer()
        with connection.cursor() as cur:
            cur.execute('SELECT value FROM provider_credentials WHERE user_email = %s', [ALICE])
            (brut,) = cur.fetchone()
        self.assertNotIn(SECRET, brut)
        self.assertTrue(brut.startswith('gAAAAA'))
        self.assertEqual(ProviderCredential.objects.get(user_email=ALICE).value, SECRET)

    def test_un_autre_utilisateur_ne_voit_pas_la_cle_d_autrui(self):
        self.deposer()
        self.assertEqual(self.bob.get(URL).json(), {'access_token_configured': False})
        # … et son oubli ne touche pas le coffre d'Alice.
        self.assertEqual(self.deposer(self.bob, '').status_code, 204)
        self.assertEqual(self.alice.get(URL).json(), {'access_token_configured': True})

    def test_chaine_vide_supprime(self):
        self.deposer()
        self.assertEqual(self.deposer(value='').status_code, 204)
        self.assertFalse(ProviderCredential.objects.filter(user_email=ALICE).exists())
        self.assertEqual(self.alice.get(URL).json(), {'access_token_configured': False})

    def test_nouvelle_valeur_remplace_l_ancienne(self):
        self.deposer()
        self.deposer(value='autre-cle')
        self.assertEqual(ProviderCredential.objects.get(user_email=ALICE).value, 'autre-cle')

    def test_fournisseur_inconnu_400(self):
        self.assertEqual(self.alice.get('/api/provider-credentials/inconnu/').status_code, 400)
        self.assertEqual(self.alice.put('/api/provider-credentials/inconnu/',
                                        {'access_token': 'x'}, format='json').status_code, 400)

    def test_champ_hors_du_fournisseur_400(self):
        resp = self.alice.put(URL, {'geoapify_key': 'x'}, format='json')
        self.assertEqual(resp.status_code, 400)
        resp = self.alice.put(URL, {'access_token': 'x', 'api_key': 'y'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(ProviderCredential.objects.exists())     # rien d'écrit à moitié

    def test_valeur_non_textuelle_ou_corps_vide_400(self):
        self.assertEqual(self.alice.put(URL, {'access_token': 12}, format='json').status_code, 400)
        self.assertEqual(self.alice.put(URL, {}, format='json').status_code, 400)

    def test_cle_facultative_du_geocodeur(self):
        url = '/api/provider-credentials/nominatim/'
        self.assertEqual(self.alice.get(url).json(), {'geoapify_key_configured': False})
        self.assertEqual(self.alice.put(url, {'geoapify_key': 'g'}, format='json').status_code, 204)
        self.assertEqual(self.alice.get(url).json(), {'geoapify_key_configured': True})

    def test_fournisseur_sans_cle(self):
        self.assertEqual(self.alice.get('/api/provider-credentials/wikitree/').json(), {})

    def test_anonyme_refuse(self):
        self.assertIn(APIClient().get(URL).status_code, (401, 403))
        self.assertIn(APIClient().put(URL, {'access_token': 'x'}, format='json').status_code,
                      (401, 403))
        self.assertFalse(ProviderCredential.objects.exists())

    def test_valeur_chiffree_sous_une_autre_cle_traitee_comme_absente(self):
        self.deposer()
        with override_settings(VAULT_ENCRYPTION_KEY=Fernet.generate_key().decode()), \
                self.assertLogs('api.fields', level=logging.WARNING) as logs:
            self.assertIsNone(ProviderCredential.objects.get(user_email=ALICE).value)
        self.assertNotIn('gAAAAA', ''.join(logs.output))          # journalisé sans la valeur


class CoffreEtFournisseurTests(FamilyTreeCase):
    """Les appels aux fournisseurs lisent le coffre de l'utilisateur courant."""

    def setUp(self):
        super().setUp()
        self.fournisseur = _FauxFournisseur()
        self.get_provider = self._patch(mock.patch('api.views.get_provider',
                                                   return_value=self.fournisseur))
        self.alice.put(URL, {'access_token': SECRET}, format='json')

    def test_recherche_recoit_la_cle_du_coffre_et_ignore_celle_du_corps(self):
        resp = self.alice.post('/api/enrich/search/', {
            'provider': 'geni', 'query': {'surname': 'Clemens'},
            'credentials': {'access_token': 'cle-du-corps'}},
            format='json', HTTP_X_PROVIDER_KEY='cle-de-l-en-tete')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.get_provider.call_args[0], ('geni', {'access_token': SECRET}))

    def test_fetch_et_import_lisent_le_coffre(self):
        self.alice.post('/api/enrich/fetch/', {'provider': 'geni', 'external_id': '1',
                                               'credentials': {'access_token': 'x'}},
                        format='json')
        self.assertEqual(self.get_provider.call_args[0], ('geni', {'access_token': SECRET}))
        resp = self.alice.post('/api/enrich/import/', {
            'provider': 'geni', 'external_id': '1', 'tree': self.tree.pk, 'with_photo': False},
            format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(self.get_provider.call_args[0], ('geni', {'access_token': SECRET}))

    def test_un_autre_utilisateur_n_utilise_pas_la_cle_d_alice(self):
        client_for(DAVE).post('/api/enrich/search/', {
            'provider': 'geni', 'query': {'surname': 'Clemens'}}, format='json')
        self.assertEqual(self.get_provider.call_args[0], ('geni', {}))

    def test_le_vrai_fournisseur_recoit_la_cle_du_coffre(self):
        # Le vrai GeniProvider, la seule couche HTTP remplacée.
        with mock.patch('api.views.get_provider', wraps=providers.get_provider), \
                mock.patch('api.providers.base.requests.get') as http:
            http.return_value.status_code = 200
            http.return_value.json.return_value = {'results': []}
            self.alice.post('/api/enrich/search/', {
                'provider': 'geni', 'query': {'surname': 'Clemens'}}, format='json')
        self.assertTrue(http.called)
        self.assertEqual(http.call_args.kwargs['params']['access_token'], SECRET)
