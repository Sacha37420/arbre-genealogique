"""Autorisation par arbre — toute la protection de cette app est ici.

Règle (``TreeOwnerMixin``) : lecture = propriétaire, invités, arbres publics ;
écriture = propriétaire et invités « éditeur » ; propriété (renommer, publier,
supprimer, partager) = propriétaire seul. Les ressources filles héritent de la
règle par leur arbre.

Acteurs : Alice possède `tree` (privé) et `public` ; Bob y est éditeur, Carol
lectrice ; Dave est un compte du realm sans aucun lien, propriétaire de `other`.
"""
import unittest
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from api import urls as api_urls
from api.models import (
    EnrichmentMatch,
    Event,
    FamilySpouse,
    ImportJob,
    Individual,
    MediaLink,
    MediaObject,
    NodeLayout,
    Tree,
    TreeShare,
    TreeViewSettings,
)
from api.providers import PersonResult, ProviderError

from .helpers import ALICE, BOB, CAROL, DAVE, FamilyTreeCase, ns

ROUTES = [prefix for prefix, _, _ in api_urls.router.registry
          if prefix not in ('trees', 'tree-shares')]
LECTURE_SEULE = {'import-jobs'}


class InventaireTests(FamilyTreeCase):
    def test_le_jeu_de_donnees_couvre_toutes_les_routes(self):
        """Une ressource ajoutée au routeur sans objet ici ferait passer la
        matrice ci-dessous à côté d'elle : ce test l'interdit."""
        self.assertEqual(sorted(ROUTES), sorted(k for k in self.objects if not k.startswith('_')))
        self.assertEqual(len(ROUTES), 22)


class MatriceParRessourceTests(FamilyTreeCase):
    """Lire / modifier / supprimer un objet existant, pour chaque ressource."""

    def url(self, prefix, objs=None):
        return f'/api/{prefix}/{(objs or self.objects)[prefix].pk}/'

    def test_lecture_du_detail(self):
        attendus = {'alice': 200, 'bob': 200, 'carol': 200, 'dave': 404}
        for prefix in ROUTES:
            for nom, code in attendus.items():
                with self.subTest(route=prefix, acteur=nom):
                    self.assertEqual(getattr(self, nom).get(self.url(prefix)).status_code, code)

    def test_la_liste_ne_montre_que_les_arbres_visibles(self):
        for prefix in ROUTES:
            with self.subTest(route=prefix):
                resp = self.dave.get(f'/api/{prefix}/')
                self.assertEqual(resp.status_code, 200)
                ids = {row['id'] for row in resp.json()}
                self.assertNotIn(self.objects[prefix].pk, ids)        # arbre privé d'autrui
                self.assertIn(self.public_objects[prefix].pk, ids)    # arbre public
                self.assertIn(self.other_objects[prefix].pk, ids)     # le sien
                # Le filtre ?tree= restreint, il n'élargit jamais.
                filtre = self.dave.get(f'/api/{prefix}/', {'tree': self.tree.pk})
                self.assertEqual(filtre.json(), [])
                # Carol, invitée en lecture, voit bien l'objet.
                vus = {row['id'] for row in self.carol.get(f'/api/{prefix}/').json()}
                self.assertIn(self.objects[prefix].pk, vus)

    def test_modification(self):
        attendus = {'alice': 200, 'bob': 200, 'carol': 404, 'dave': 404}
        for prefix in ROUTES:
            for nom, code in attendus.items():
                if prefix in LECTURE_SEULE:
                    code = 405
                with self.subTest(route=prefix, acteur=nom):
                    resp = getattr(self, nom).patch(self.url(prefix), {}, format='json')
                    self.assertEqual(resp.status_code, code)

    def test_suppression(self):
        attendus = {'carol': 404, 'dave': 404, 'bob': 204, 'alice': 204}
        for prefix in ROUTES:
            modele = type(self.objects[prefix])
            for nom, code in attendus.items():
                if prefix in LECTURE_SEULE:
                    code = 405
                with self.subTest(route=prefix, acteur=nom):
                    sid = transaction.savepoint()
                    resp = getattr(self, nom).delete(self.url(prefix))
                    self.assertEqual(resp.status_code, code)
                    existe = modele.objects.filter(pk=self.objects[prefix].pk).exists()
                    self.assertEqual(existe, code != 204)
                    transaction.savepoint_rollback(sid)

    def test_arbre_public_lisible_par_tous_modifiable_par_personne_d_autre(self):
        for prefix in ROUTES:
            with self.subTest(route=prefix):
                url = self.url(prefix, self.public_objects)
                self.assertEqual(self.dave.get(url).status_code, 200)
                self.assertEqual(self.dave.patch(url, {}, format='json').status_code,
                                 405 if prefix in LECTURE_SEULE else 404)
                self.assertEqual(self.dave.delete(url).status_code,
                                 405 if prefix in LECTURE_SEULE else 404)
                self.assertTrue(type(self.public_objects[prefix]).objects
                                .filter(pk=self.public_objects[prefix].pk).exists())


class CreationTests(FamilyTreeCase):
    """Le filtrage par queryset ne protège que l'existant : une création désigne
    elle-même son arbre (ou son parent), il faut donc la vérifier à part."""

    def payloads(self, objs):
        tree = objs['individuals'].tree_id
        return {
            'individuals': {'tree': tree, 'givn': 'Luc', 'surn': 'Martin'},
            'names': {'individual': objs['individuals'].pk, 'givn': 'Jeannot'},
            'families': {'tree': tree},
            'family-spouses': {'family': objs['_famille2'].pk, 'individual': objs['_mere'].pk,
                               'role': 'WIFE'},
            'family-children': {'family': objs['_famille2'].pk, 'individual': objs['_enfant'].pk},
            'events': {'tree': tree, 'individual': objs['_mere'].pk, 'tag': 'BIRT',
                       'date_raw': '1902'},
            'places': {'tree': tree, 'name': 'Chinon'},
            'media': {'tree': tree, 'title': 'Lien', 'external_url': 'https://example.org/a.jpg'},
            'sources': {'tree': tree, 'title': 'Acte'},
            'repositories': {'tree': tree, 'name': 'Mairie'},
            'citations': {'source': objs['sources'].pk},
            'shared-notes': {'tree': tree, 'text': 'n'},
            'node-styles': {'tree': tree, 'name': 'S2'},
            'style-rules': {'tree': tree, 'name': 'R2', 'style': objs['node-styles'].pk},
            'edge-styles': {'tree': tree, 'name': 'E2'},
            'card-templates': {'tree': tree, 'name': 'G2', 'kind': 'MINI'},
            'node-layouts': {'individual': objs['_mere'].pk, 'x': 5, 'y': 5},
            'family-layouts': {'family': objs['_famille2'].pk, 'x': 5, 'y': 5},
            'custom-fields': {'tree': tree, 'key': 'grade', 'label': 'Grade'},
            'enrichment-matches': {'tree': tree, 'provider': 'wikitree', 'external_id': 'Y-2'},
        }

    def test_le_proprietaire_et_l_editeur_creent(self):
        for nom in ('alice', 'bob'):
            for prefix, data in self.payloads(self.objects).items():
                with self.subTest(route=prefix, acteur=nom):
                    sid = transaction.savepoint()
                    resp = getattr(self, nom).post(f'/api/{prefix}/', data, format='json')
                    self.assertEqual(resp.status_code, 201, resp.content)
                    transaction.savepoint_rollback(sid)

    def test_le_lecteur_et_l_etranger_ne_creent_rien(self):
        for arbre, objs in ((self.tree, self.objects), (self.public, self.public_objects)):
            for nom in ('carol', 'dave'):
                for prefix, data in self.payloads(objs).items():
                    modele = type(objs[prefix])
                    with self.subTest(arbre=arbre.name, route=prefix, acteur=nom):
                        avant = modele.objects.count()
                        resp = getattr(self, nom).post(f'/api/{prefix}/', data, format='json')
                        self.assertEqual(resp.status_code, 403, resp.content)
                        self.assertEqual(modele.objects.count(), avant)
        self.assertEqual(self.storage.writes(), [])

    def test_on_ne_deplace_pas_un_objet_vers_l_arbre_d_autrui(self):
        # Dave modifie SES objets pour les rattacher à l'arbre d'Alice.
        cas = {
            'individuals': {'tree': self.tree.pk},
            'places': {'tree': self.tree.pk, 'name': 'Ailleurs'},
            'media': {'tree': self.tree.pk},
            'names': {'individual': self.objects['individuals'].pk},
            'family-spouses': {'family': self.objects['families'].pk},
            'node-layouts': {'individual': self.objects['_mere'].pk},
            'citations': {'source': self.objects['sources'].pk},
        }
        for prefix, data in cas.items():
            with self.subTest(route=prefix):
                objet = self.other_objects[prefix]
                resp = self.dave.patch(f'/api/{prefix}/{objet.pk}/', data, format='json')
                self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(Individual.objects.filter(tree=self.tree).count(), 3)

    def test_l_invite_en_lecture_ne_deplace_rien_hors_de_l_arbre(self):
        resp = self.carol.patch(f"/api/individuals/{self.objects['individuals'].pk}/",
                                {'note': 'x'}, format='json')
        self.assertEqual(resp.status_code, 404)

    # DÉFAUT (rapport, D2) — MediaLinkViewSet.perform_create remplace celui
    # d'OwnedTreeMixin sans appeler check_tree : la création d'un lien média
    # n'est soumise à AUCUN contrôle d'écriture.
    @unittest.expectedFailure
    def test_le_lecteur_ne_cree_pas_de_lien_media(self):
        data = {'media': self.objects['media'].pk, 'individual': self.objects['_mere'].pk}
        resp = self.carol.post('/api/media-links/', data, format='json')
        self.assertEqual(resp.status_code, 403)

    # DÉFAUT (D2, conséquence) : une invitée en LECTURE change le portrait d'une
    # personne de l'arbre — le lien primaire qu'elle crée déclasse l'existant.
    @unittest.expectedFailure
    def test_le_lecteur_ne_change_pas_le_portrait(self):
        self.carol.post('/api/media-links/', {
            'media': self.objects['media'].pk, 'individual': self.objects['individuals'].pk,
            'is_primary': True,
        }, format='json')
        self.objects['media-links'].refresh_from_db()
        self.assertTrue(self.objects['media-links'].is_primary)

    # DÉFAUT (D2, conséquence) : un compte sans aucun droit sur l'arbre rattache
    # un média privé d'autrui à sa propre personne ; la réponse lui donne le
    # titre du média et son chemin dans storage.
    @unittest.expectedFailure
    def test_l_etranger_ne_lie_pas_le_media_d_un_arbre_prive(self):
        resp = self.dave.post('/api/media-links/', {
            'media': self.objects['media'].pk, 'individual': self.other_objects['individuals'].pk,
        }, format='json')
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertNotIn('Portrait secret', resp.content.decode())


class ReferencesCroiseesTests(FamilyTreeCase):
    """Un objet de MON arbre ne doit pas pouvoir désigner un objet d'un arbre
    auquel je n'ai pas accès : la vérification porte sur l'arbre de l'objet
    écrit, il reste les AUTRES clés étrangères du même corps de requête."""

    def post(self, prefix, data):
        return self.dave.post(f'/api/{prefix}/', data, format='json')

    def test_ajouter_un_proche_venu_d_un_autre_arbre_est_refuse(self):
        # Les actions dédiées, elles, filtrent bien sur l'arbre.
        mine, theirs = self.other_objects, self.objects
        r1 = self.dave.post(f"/api/individuals/{mine['individuals'].pk}/add_relative/",
                            {'relation': 'CHILD', 'individual': theirs['individuals'].pk},
                            format='json')
        r2 = self.dave.post(f"/api/families/{mine['families'].pk}/add_spouse/",
                            {'individual': theirs['individuals'].pk}, format='json')
        r3 = self.dave.post(f"/api/families/{mine['families'].pk}/add_child/",
                            {'individual': theirs['individuals'].pk}, format='json')
        self.assertEqual([r1.status_code, r2.status_code, r3.status_code], [400, 400, 400])
        self.assertFalse(FamilySpouse.objects.filter(
            family__tree=self.other, individual__tree=self.tree).exists())

    def test_les_positions_en_lot_ignorent_les_autres_arbres(self):
        cible = self.objects['_mere']
        resp = self.dave.post(f'/api/trees/{self.other.pk}/layout/', {'positions': [
            {'individual': cible.pk, 'x': 9, 'y': 9},
            {'family': self.objects['_famille2'].pk, 'x': 9, 'y': 9},
            {'individual': self.other_objects['_mere'].pk, 'x': 7, 'y': 8},
        ]}, format='json')
        self.assertEqual(resp.json(), {'saved': 1})
        self.assertFalse(NodeLayout.objects.filter(individual=cible).exists())
        self.assertEqual(NodeLayout.objects.get(individual=self.other_objects['_mere']).x, 7)

    def test_l_apercu_de_gabarit_ne_sort_pas_de_son_arbre(self):
        resp = self.dave.get(f"/api/card-templates/{self.other_objects['card-templates'].pk}/preview/",
                             {'individual': self.objects['individuals'].pk})
        self.assertEqual(resp.status_code, 404)

    # DÉFAUT (rapport, D3) — les sérialiseurs acceptent n'importe quelle clé
    # primaire pour les clés étrangères secondaires, et _tree_of ne remonte que
    # par `tree_path`. Chaque cas ci-dessous est accepté (201/200) aujourd'hui.
    CAS = {
        'conjoint':   ('family-spouses', lambda m, t: {'family': m['_famille2'].pk,
                                                       'individual': t['individuals'].pk}),
        'enfant':     ('family-children', lambda m, t: {'family': m['_famille2'].pk,
                                                        'individual': t['_enfant'].pk}),
        'événement d’une personne': ('events', lambda m, t: {
            'tree': m['individuals'].tree_id, 'individual': t['_mere'].pk, 'tag': 'DEAT'}),
        'événement d’une famille': ('events', lambda m, t: {
            'tree': m['individuals'].tree_id, 'family': t['families'].pk, 'tag': 'MARR'}),
        'lien média vers une personne': ('media-links', lambda m, t: {
            'media': m['media'].pk, 'individual': t['_mere'].pk}),
        'citation':   ('citations', lambda m, t: {'source': m['sources'].pk,
                                                  'individual': t['individuals'].pk}),
        'règle de style': ('style-rules', lambda m, t: {
            'tree': m['individuals'].tree_id, 'name': 'r', 'style': t['node-styles'].pk}),
        'style forcé': ('node-layouts', lambda m, t: {
            'individual': m['_mere'].pk, 'style_override': t['node-styles'].pk}),
        'dépôt d’une source': ('sources', lambda m, t: {
            'tree': m['individuals'].tree_id, 'title': 's', 'repository': t['repositories'].pk}),
        'candidat d’enrichissement': ('enrichment-matches', lambda m, t: {
            'tree': m['individuals'].tree_id, 'provider': 'p', 'external_id': 'e',
            'individual': t['individuals'].pk}),
    }

    def _cas(self, nom):
        prefix, build = self.CAS[nom]
        resp = self.post(prefix, build(self.other_objects, self.objects))
        self.assertIn(resp.status_code, (400, 403), f'{nom} : {resp.status_code}')

    def test_les_dix_cas_sont_connus(self):
        self.assertEqual(len(self.CAS), 10)


def _defaut(nom):
    @unittest.expectedFailure
    def test(self):
        self._cas(nom)
    return test


for _i, _nom in enumerate(ReferencesCroiseesTests.CAS):
    setattr(ReferencesCroiseesTests, f'test_reference_croisee_{_i:02d}_refusee', _defaut(_nom))


class ConsequencesDesReferencesCroiseesTests(FamilyTreeCase):
    """Ce que D3 permet réellement, sur un arbre privé auquel Dave n'a pas accès."""

    # Lecture : Dave « marie » une personne de l'arbre d'Alice dans SA famille,
    # puis lit son nom par /relations/ et le graphe de son propre arbre.
    @unittest.expectedFailure
    def test_le_nom_d_une_personne_d_un_arbre_prive_ne_fuit_pas(self):
        secret = self.objects['individuals']          # « Jean Martin<id> »
        mien = self.other_objects['_mere']
        famille = self.other_objects['_famille2']
        self.dave.post('/api/family-spouses/', {'family': famille.pk, 'individual': mien.pk},
                       format='json')
        self.dave.post('/api/family-spouses/', {'family': famille.pk, 'individual': secret.pk},
                       format='json')
        relations = self.dave.get(f'/api/individuals/{mien.pk}/relations/')
        self.assertNotIn(f'Martin{self.tree.pk}', relations.content.decode())

    # Écriture : un événement « décès » posé sur la personne d'autrui la fait
    # passer pour décédée dans l'arbre de son propriétaire.
    @unittest.expectedFailure
    def test_on_ne_declare_pas_decedee_la_personne_d_un_autre_arbre(self):
        cible = self.objects['_mere']
        self.dave.post('/api/events/', {'tree': self.other.pk, 'individual': cible.pk,
                                        'tag': 'DEAT'}, format='json')
        cible.refresh_from_db()
        self.assertTrue(cible.is_living)
        self.assertFalse(Event.objects.filter(individual=cible, tag='DEAT').exists())

    # Écriture : les réglages de vue acceptent un individu racine d'un autre arbre.
    @unittest.expectedFailure
    def test_les_reglages_de_vue_restent_dans_l_arbre(self):
        resp = self.dave.patch(f'/api/trees/{self.other.pk}/settings/',
                               {'root_individual': self.objects['individuals'].pk}, format='json')
        self.assertEqual(resp.status_code, 400)


class ArbreTests(FamilyTreeCase):

    def test_liste_des_arbres_et_role_annonce(self):
        def vus(client):
            return {t['id']: t['my_role'] for t in client.get('/api/trees/').json()}
        self.assertEqual(vus(self.alice), {self.tree.pk: 'OWNER', self.public.pk: 'OWNER'})
        self.assertEqual(vus(self.bob), {self.tree.pk: 'EDITOR', self.public.pk: 'VIEWER'})
        self.assertEqual(vus(self.carol), {self.tree.pk: 'VIEWER', self.public.pk: 'VIEWER'})
        self.assertEqual(vus(self.dave), {self.other.pk: 'OWNER', self.public.pk: 'VIEWER'})

    def test_l_arbre_prive_est_introuvable_pour_un_etranger(self):
        self.assertEqual(self.dave.get(f'/api/trees/{self.tree.pk}/').status_code, 404)
        self.assertEqual(self.carol.get(f'/api/trees/{self.tree.pk}/').status_code, 200)

    def test_seul_le_proprietaire_renomme_publie_supprime(self):
        url = f'/api/trees/{self.tree.pk}/'
        for nom in ('bob', 'carol', 'dave'):
            with self.subTest(acteur=nom):
                client = getattr(self, nom)
                self.assertEqual(client.patch(url, {'name': 'pris'}, format='json').status_code, 404)
                self.assertEqual(client.patch(url, {'is_public': True}, format='json').status_code, 404)
                self.assertEqual(client.put(url, {'name': 'pris'}, format='json').status_code, 404)
                self.assertEqual(client.delete(url).status_code, 404)
        self.tree.refresh_from_db()
        self.assertEqual((self.tree.name, self.tree.is_public), ('Famille Martin', False))
        self.assertEqual(self.alice.patch(url, {'name': 'Martin'}, format='json').status_code, 200)
        self.assertEqual(self.alice.delete(url).status_code, 204)
        self.assertFalse(Tree.objects.filter(pk=self.tree.pk).exists())

    def test_arbre_public_personne_d_autre_ne_le_modifie(self):
        url = f'/api/trees/{self.public.pk}/'
        self.assertEqual(self.dave.get(url).status_code, 200)
        self.assertEqual(self.dave.patch(url, {'is_public': False}, format='json').status_code, 404)
        self.assertEqual(self.dave.delete(url).status_code, 404)

    def test_le_proprietaire_vient_du_jeton_jamais_du_corps(self):
        resp = self.dave.post('/api/trees/', {'name': 'Neuf', 'owner_email': ALICE},
                              format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['owner_email'], DAVE)
        self.assertEqual(resp.json()['my_role'], 'OWNER')
        neuf = resp.json()['id']
        # … et il ne se cède ni ne se vole par une modification.
        self.dave.patch(f'/api/trees/{neuf}/', {'owner_email': ALICE}, format='json')
        self.assertEqual(Tree.objects.get(pk=neuf).owner_email, DAVE)
        self.alice.patch(f'/api/trees/{self.tree.pk}/', {'owner_email': DAVE}, format='json')
        self.assertEqual(Tree.objects.get(pk=self.tree.pk).owner_email, ALICE)

    def test_un_arbre_neuf_est_affichable_et_prive(self):
        neuf = self.dave.post('/api/trees/', {'name': 'Neuf'}, format='json').json()
        self.assertFalse(neuf['is_public'])
        graphe = self.dave.get(f"/api/trees/{neuf['id']}/graph/").json()
        self.assertIsNotNone(graphe['mini_template'])
        self.assertIsNotNone(graphe['full_template'])
        self.assertEqual(self.alice.get(f"/api/trees/{neuf['id']}/").status_code, 404)

    def test_actions_de_lecture(self):
        attendus = {'alice': 200, 'bob': 200, 'carol': 200, 'dave': 404}
        for action in ('graph', 'gedcom', 'settings'):
            for nom, code in attendus.items():
                with self.subTest(action=action, acteur=nom):
                    resp = getattr(self, nom).get(f'/api/trees/{self.tree.pk}/{action}/')
                    self.assertEqual(resp.status_code, code)
            with self.subTest(action=action, arbre='public'):
                self.assertEqual(
                    self.dave.get(f'/api/trees/{self.public.pk}/{action}/').status_code, 200)

    def test_le_graphe_ne_contient_que_son_arbre(self):
        graphe = self.carol.get(f'/api/trees/{self.tree.pk}/graph/').json()
        ids = {n['id'] for n in graphe['nodes']}
        self.assertEqual(ids, set(Individual.objects.filter(tree=self.tree)
                                  .values_list('pk', flat=True)))
        self.assertNotIn(self.other_objects['individuals'].pk, ids)

    def test_actions_d_ecriture(self):
        gedcom = '0 HEAD\n0 INDI\n1 NAME Zoé /Importée/\n0 TRLR\n'
        actions = {
            'import_gedcom': ('post', {'content': gedcom}, 201),
            'layout': ('post', {'positions': []}, 200),
            'settings': ('patch', {'zoom': 2}, 200),
            'geocode': ('post', {}, 200),
        }
        for action, (methode, corps, ok) in actions.items():
            url = f'/api/trees/{self.tree.pk}/{action}/'
            for nom, code in {'carol': (403, 404), 'dave': (404,), 'bob': (ok,), 'alice': (ok,)}.items():
                with self.subTest(action=action, acteur=nom):
                    with mock.patch('api.views.Geocoder') as geocoder:
                        geocoder.return_value.geocode.return_value = None
                        resp = getattr(getattr(self, nom), methode)(url, corps, format='json')
                    self.assertIn(resp.status_code, code, resp.content)
            with self.subTest(action=action, arbre='public'):
                resp = getattr(self.dave, methode)(
                    f'/api/trees/{self.public.pk}/{action}/', corps, format='json')
                self.assertIn(resp.status_code, (403, 404))
        # Rien n'a été importé par les refus : deux imports seulement (Bob, Alice).
        self.assertEqual(ImportJob.objects.filter(tree=self.tree).count(), 1 + 2)
        self.assertEqual(Individual.objects.filter(tree=self.public).count(), 3)
        self.assertEqual(TreeViewSettings.objects.get(tree=self.tree).zoom, 2)

    def test_actions_sur_une_personne(self):
        pere = self.objects['individuals']
        for action in ('timeline', 'relations'):
            url = f'/api/individuals/{pere.pk}/{action}/'
            with self.subTest(action=action):
                self.assertEqual(self.carol.get(url).status_code, 200)
                self.assertEqual(self.dave.get(url).status_code, 404)
        url = f'/api/individuals/{pere.pk}/add_relative/'
        corps = {'relation': 'SIBLING', 'givn': 'Louis', 'surn': 'Martin'}
        self.assertEqual(self.carol.post(url, corps, format='json').status_code, 404)
        self.assertEqual(self.dave.post(url, corps, format='json').status_code, 404)
        self.assertEqual(Individual.objects.filter(tree=self.tree).count(), 3)
        self.assertEqual(self.bob.post(url, corps, format='json').status_code, 201)

    def test_actions_sur_une_famille_et_un_lieu(self):
        famille, lieu = self.objects['families'], self.objects['places']
        corps = {'individual': self.objects['_enfant'].pk}
        for nom in ('carol', 'dave'):
            client = getattr(self, nom)
            with self.subTest(acteur=nom):
                self.assertEqual(client.post(f'/api/families/{famille.pk}/add_spouse/', corps,
                                             format='json').status_code, 404)
                self.assertEqual(client.post(f'/api/families/{famille.pk}/add_child/', corps,
                                             format='json').status_code, 404)
                with mock.patch('api.views.Geocoder') as geocoder:
                    self.assertEqual(client.post(f'/api/places/{lieu.pk}/geocode/', {},
                                                 format='json').status_code, 404)
                    geocoder.assert_not_called()


class PartageTests(FamilyTreeCase):
    """Les partages : visibles et modifiables par le seul propriétaire."""

    def test_seul_le_proprietaire_voit_les_partages(self):
        emails = lambda c: sorted(s['email'] for s in c.get('/api/tree-shares/').json())
        self.assertEqual(emails(self.alice), [BOB, CAROL])
        for nom in ('bob', 'carol', 'dave'):
            with self.subTest(acteur=nom):
                self.assertEqual(emails(getattr(self, nom)), [])
                self.assertEqual(
                    getattr(self, nom).get(f'/api/tree-shares/{self.share_carol.pk}/').status_code,
                    404)

    def test_personne_d_autre_n_invite(self):
        # L'éditeur ne s'élève pas, l'étranger ne s'invite pas, y compris sur un arbre public.
        for nom, email in (('bob', BOB), ('carol', CAROL), ('dave', DAVE)):
            for arbre in (self.tree, self.public):
                with self.subTest(acteur=nom, arbre=arbre.name):
                    resp = getattr(self, nom).post('/api/tree-shares/', {
                        'tree': arbre.pk, 'email': email, 'role': 'EDITOR'}, format='json')
                    self.assertEqual(resp.status_code, 403)
        self.assertEqual(TreeShare.objects.count(), 2)
        self.assertEqual(TreeShare.objects.get(email=CAROL).role, 'VIEWER')
        self.assertEqual(self.storage.writes(), [])

    def test_personne_d_autre_ne_modifie_ni_ne_retire_un_partage(self):
        url = f'/api/tree-shares/{self.share_carol.pk}/'
        for nom in ('bob', 'carol', 'dave'):
            with self.subTest(acteur=nom):
                client = getattr(self, nom)
                self.assertEqual(client.patch(url, {'role': 'EDITOR'}, format='json').status_code, 404)
                self.assertEqual(client.delete(url).status_code, 404)
        self.assertEqual(TreeShare.objects.get(pk=self.share_carol.pk).role, 'VIEWER')

    def test_inviter_ouvre_l_acces_et_le_retirer_le_ferme(self):
        url = f'/api/trees/{self.tree.pk}/'
        self.assertEqual(self.dave.get(url).status_code, 404)
        resp = self.alice.post('/api/tree-shares/', {
            'tree': self.tree.pk, 'email': '  Dave@Example.ORG ', 'invited_by': 'faux@x.org',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['email'], DAVE)             # normalisé : c'est la clé d'identité
        self.assertEqual(resp.json()['role'], 'VIEWER')          # lecture par défaut
        self.assertEqual(resp.json()['invited_by'], ALICE)       # jamais celui du corps
        self.assertEqual(self.dave.get(url).status_code, 200)
        self.assertEqual(self.dave.patch(f"/api/individuals/{self.objects['individuals'].pk}/",
                                         {'note': 'x'}, format='json').status_code, 404)
        self.assertEqual(self.alice.delete(f"/api/tree-shares/{resp.json()['id']}/").status_code, 204)
        self.assertEqual(self.dave.get(url).status_code, 404)

    def test_reinviter_change_le_role_sans_doublon(self):
        resp = self.alice.post('/api/tree-shares/', {
            'tree': self.tree.pk, 'email': CAROL, 'role': 'EDITOR'}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(TreeShare.objects.filter(tree=self.tree, email=CAROL).count(), 1)
        self.assertEqual(self.carol.patch(f"/api/individuals/{self.objects['individuals'].pk}/",
                                          {'note': 'x'}, format='json').status_code, 200)

    def test_on_ne_s_invite_pas_soi_meme(self):
        resp = self.alice.post('/api/tree-shares/', {'tree': self.tree.pk, 'email': ALICE},
                               format='json')
        self.assertEqual(resp.status_code, 400)

    def test_passer_de_l_edition_a_la_lecture_retire_l_ecriture(self):
        cible = f"/api/individuals/{self.objects['individuals'].pk}/"
        self.assertEqual(self.bob.patch(cible, {'note': 'a'}, format='json').status_code, 200)
        resp = self.alice.patch(f'/api/tree-shares/{self.share_bob.pk}/', {'role': 'VIEWER'},
                                format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.bob.patch(cible, {'note': 'b'}, format='json').status_code, 404)
        self.assertEqual(self.bob.get(cible).status_code, 200)

    def test_storage_en_panne_le_partage_n_est_pas_cree(self):
        # Sinon l'app dirait « partagé » alors que storage refuserait les photos —
        # ou, pire au retrait, storage continuerait de les servir.
        self.storage.down = True
        resp = self.alice.post('/api/tree-shares/', {'tree': self.tree.pk, 'email': DAVE},
                               format='json')
        self.assertEqual(resp.status_code, 502)
        self.assertFalse(TreeShare.objects.filter(email=DAVE).exists())
        resp = self.alice.delete(f'/api/tree-shares/{self.share_carol.pk}/')
        self.assertEqual(resp.status_code, 502)
        self.assertTrue(TreeShare.objects.filter(pk=self.share_carol.pk).exists())
        resp = self.alice.patch(f'/api/tree-shares/{self.share_carol.pk}/', {'role': 'EDITOR'},
                                format='json')
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(TreeShare.objects.get(pk=self.share_carol.pk).role, 'VIEWER')

    def test_un_partage_ne_se_deplace_pas_vers_l_arbre_d_autrui(self):
        # Corrigé (D4) : Dave créait un partage sur son propre arbre puis le
        # déplaçait sur l'arbre d'Alice, et devenait éditeur d'un arbre privé
        # qui ne lui avait jamais été partagé.
        mien = self.dave.post('/api/tree-shares/', {
            'tree': self.other.pk, 'email': 'complice@example.org', 'role': 'EDITOR'},
            format='json').json()
        resp = self.dave.patch(f"/api/tree-shares/{mien['id']}/",
                               {'tree': self.tree.pk, 'email': DAVE}, format='json')
        self.assertIn(resp.status_code, (400, 403), resp.content)
        self.assertFalse(TreeShare.objects.filter(tree=self.tree, email=DAVE).exists())
        self.assertEqual(self.dave.get(f'/api/trees/{self.tree.pk}/').status_code, 404)
        self.assertNotIn(DAVE, self.storage.members.get(ns(self.tree), {}))
        # Même refus sans changer l'adresse, en PUT, et même vers un de SES arbres.
        for corps in ({'tree': self.tree.pk}, {'tree': self.public.pk, 'role': 'EDITOR'}):
            resp = self.dave.patch(f"/api/tree-shares/{mien['id']}/", corps, format='json')
            self.assertEqual(resp.status_code, 400)
        resp = self.dave.put(f"/api/tree-shares/{mien['id']}/", {
            'tree': self.tree.pk, 'email': DAVE, 'role': 'EDITOR'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(TreeShare.objects.get(pk=mien['id']).tree_id, self.other.pk)
        # Renvoyer le même arbre (ce que fait un formulaire complet) reste permis.
        resp = self.dave.patch(f"/api/tree-shares/{mien['id']}/",
                               {'tree': self.other.pk, 'role': 'VIEWER'}, format='json')
        self.assertEqual(resp.status_code, 200)
        # … et le propriétaire ne se rétrograde pas en invité de son propre arbre.
        resp = self.dave.patch(f"/api/tree-shares/{mien['id']}/", {'email': DAVE}, format='json')
        self.assertEqual(resp.status_code, 400)


class _FauxFournisseur:
    def __init__(self):
        self.appels = []

    def search(self, query):
        self.appels.append(('search', query))
        return [PersonResult(provider='wikitree', external_id='Clemens-1', given_name='Sam',
                             surname='Clemens', url='https://example.org/Clemens-1', score=0.9)]

    def fetch(self, external_id):
        self.appels.append(('fetch', external_id))
        return PersonResult(provider='wikitree', external_id=external_id, given_name='Sam',
                            surname='Clemens', birth_date='1835-11-30', death_date='1910')


class EnrichissementTests(FamilyTreeCase):
    """Les trois endpoints d'import externe écrivent dans un arbre désigné par le corps."""

    def setUp(self):
        super().setUp()
        self.fournisseur = _FauxFournisseur()
        self.get_provider = self._patch(mock.patch('api.views.get_provider',
                                                   return_value=self.fournisseur))

    def test_import_dans_l_arbre_d_autrui_refuse_avant_tout_appel_externe(self):
        for nom in ('bob', 'carol', 'dave'):   # l'import est réservé au propriétaire
            for arbre in (self.tree, self.public):
                with self.subTest(acteur=nom, arbre=arbre.name):
                    resp = getattr(self, nom).post('/api/enrich/import/', {
                        'provider': 'wikitree', 'external_id': 'Clemens-1', 'tree': arbre.pk,
                        'credentials': {'access_token': 's3cret'}}, format='json')
                    self.assertEqual(resp.status_code, 403)
        self.get_provider.assert_not_called()
        self.assertEqual(Individual.objects.filter(tree__in=[self.tree, self.public]).count(), 6)

    def test_import_par_le_proprietaire(self):
        resp = self.alice.post('/api/enrich/import/', {
            'provider': 'wikitree', 'external_id': 'Clemens-1', 'tree': self.tree.pk},
            format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        cree = Individual.objects.get(pk=resp.json()['individual']['id'])
        self.assertEqual(cree.tree_id, self.tree.pk)
        self.assertFalse(cree.is_living)
        self.assertEqual(EnrichmentMatch.objects.get(external_id='Clemens-1').status, 'IMPORTED')

    def test_la_cible_d_un_import_doit_etre_dans_l_arbre(self):
        resp = self.dave.post('/api/enrich/import/', {
            'provider': 'wikitree', 'external_id': 'Clemens-1', 'tree': self.other.pk,
            'target': self.objects['individuals'].pk}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Event.objects.filter(individual=self.objects['individuals'],
                                              tag='DEAT').exists())

    def test_recherche_enregistree_dans_l_arbre_d_autrui_refusee(self):
        resp = self.dave.post('/api/enrich/search/', {
            'provider': 'wikitree', 'query': {'surname': 'Clemens'}, 'tree': self.tree.pk,
            'save': True}, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(EnrichmentMatch.objects.filter(external_id='Clemens-1').exists())

    def test_recherche_sans_arbre_ouverte_a_tout_compte_et_sans_trace(self):
        resp = self.dave.post('/api/enrich/search/', {
            'provider': 'wikitree', 'query': {'surname': 'Clemens', 'limit': 5000},
            'credentials': {'access_token': 's3cret'}}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['count'], 1)
        self.assertEqual(self.fournisseur.appels[0][1].limit, 50)     # plafond serveur
        self.assertFalse(EnrichmentMatch.objects.filter(external_id='Clemens-1').exists())

    def test_les_cles_d_api_ne_sont_jamais_enregistrees(self):
        self.alice.post('/api/enrich/search/', {
            'provider': 'wikitree', 'query': {'surname': 'Clemens'}, 'tree': self.tree.pk,
            'save': True, 'credentials': {'access_token': 's3cret-jeton'}},
            format='json', HTTP_X_PROVIDER_KEY='s3cret-entete')
        candidat = EnrichmentMatch.objects.get(tree=self.tree, external_id='Clemens-1')
        self.assertNotIn('s3cret', str(candidat.payload))
        # … mais elles sont bien passées au fournisseur, y compris par l'en-tête.
        self.assertEqual(self.get_provider.call_args[0][1],
                         {'access_token': 's3cret-jeton', 'api_key': 's3cret-entete'})
        lu = self.alice.get('/api/enrichment-matches/').content.decode()
        self.assertNotIn('s3cret', lu)

    def test_erreur_du_fournisseur_rendue_avec_son_code(self):
        self.get_provider.side_effect = ProviderError('clé refusée', status=401)
        resp = self.alice.post('/api/enrich/fetch/', {'provider': 'geni', 'external_id': '1'},
                               format='json')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()['detail'], 'clé refusée')

    def test_fournisseur_inconnu(self):
        from api.providers import get_provider
        self.get_provider.side_effect = get_provider
        resp = self.alice.post('/api/enrich/fetch/', {'provider': 'nimporte', 'external_id': '1'},
                               format='json')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.network.call_count, 0)


class MediaAutorisationTests(FamilyTreeCase):

    def fichier(self):
        return SimpleUploadedFile('photo.png', b'\x89PNG-octets', content_type='image/png')

    def test_envoi_de_fichier_dans_l_arbre_d_autrui_refuse_sans_rien_deposer(self):
        for nom in ('carol', 'dave'):
            for arbre in (self.tree, self.public):
                with self.subTest(acteur=nom, arbre=arbre.name):
                    resp = getattr(self, nom).post(
                        '/api/media/', {'tree': arbre.pk, 'file': self.fichier()},
                        format='multipart')
                    self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.storage.writes(), [])
        self.assertEqual(MediaObject.objects.filter(tree__in=[self.tree, self.public]).count(), 2)

    def test_envoi_en_base64_dans_l_arbre_d_autrui_refuse_sans_rien_deposer(self):
        for nom in ('carol', 'dave'):
            with self.subTest(acteur=nom):
                resp = getattr(self, nom).post('/api/media/', {
                    'tree': self.tree.pk, 'title': 'x', 'data_base64': 'QUJD'}, format='json')
                self.assertEqual(resp.status_code, 403)
        self.assertEqual(MediaObject.objects.filter(tree=self.tree).count(), 1)

        # Le contrôle précède le dépôt : aucun octet n'est parti vers storage.
        self.assertEqual(self.storage.writes(), [])

    def test_l_existence_d_un_lieu_ne_se_devine_pas(self):
        # Constat consigné au rapport (O1) : un nom déjà pris répond 400 « unique
        # set » avant le 403 — un étranger peut ainsi tester la présence d'un
        # nom de lieu dans un arbre privé. Rien n'est créé dans les deux cas.
        for nom in ('Tours, Indre-et-Loire, France', 'Inconnu'):
            resp = self.dave.post('/api/places/', {'tree': self.tree.pk, 'name': nom},
                                  format='json')
            self.assertIn(resp.status_code, (400, 403))
        self.assertEqual(self.tree.places.count(), 1)

    def test_contenu_du_media(self):
        url = f"/api/media/{self.objects['media'].pk}/file/"
        for nom in ('alice', 'bob', 'carol'):
            with self.subTest(acteur=nom):
                resp = getattr(self, nom).get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(b''.join(resp.streaming_content), b'JPEG')
                self.assertEqual(resp['Content-Type'], 'image/jpeg')
                self.assertIn('private', resp['Cache-Control'])
        avant = len(self.storage.calls)
        self.assertEqual(self.dave.get(url).status_code, 404)
        self.assertEqual(len(self.storage.calls), avant)       # storage n'est même pas interrogé

    def test_contenu_lu_dans_l_espace_de_son_arbre(self):
        # Même chemin logique dans deux arbres : chacun lit le sien.
        self.storage.files[ns(self.other)]['media/abc-portrait.jpg'] = b'DAVE'
        resp = self.dave.get(f"/api/media/{self.other_objects['media'].pk}/file/")
        self.assertEqual(b''.join(resp.streaming_content), b'DAVE')

    def test_contenu_d_un_arbre_public_lisible_par_tout_compte(self):
        resp = self.dave.get(f"/api/media/{self.public_objects['media'].pk}/file/")
        self.assertEqual(resp.status_code, 200)

    def test_media_externe_et_storage_en_panne(self):
        externe = MediaObject.objects.create(tree=self.tree, external_url='https://example.org/a.jpg')
        resp = self.alice.get(f'/api/media/{externe.pk}/file/')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['external_url'], 'https://example.org/a.jpg')
        self.storage.down = True
        resp = self.alice.get(f"/api/media/{self.objects['media'].pk}/file/")
        self.assertEqual(resp.status_code, 502)

    def test_supprimer_un_lien_d_un_arbre_en_lecture_seule(self):
        lien = self.objects['media-links']
        self.assertEqual(self.carol.delete(f'/api/media-links/{lien.pk}/').status_code, 404)
        self.assertTrue(MediaLink.objects.filter(pk=lien.pk).exists())
        self.assertEqual(self.storage.writes(), [])
