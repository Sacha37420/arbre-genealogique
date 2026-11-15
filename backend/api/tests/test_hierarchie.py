"""Chantier « storage-hierarchie » (contrat v2) : partages d'arbre rangés sous
le conteneur ``arbre-genealogique`` (I1) et commande de rattachement de
l'existant (I3), contre le faux storage de ``helpers``."""
import io
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from api import storage_client
from api.models import MediaObject, Tree

from .helpers import ALICE, BOB, LabTestCase, ns

C = storage_client.CONTAINER


class Base(LabTestCase):
    def commande(self, *args):
        """(code de sortie, sortie) — 0 si la commande se termine normalement."""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                call_command('rattacher_partages_storage', *args, stdout=out, stderr=err)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue() + err.getvalue()

    def patches(self):
        return [c for c in self.storage.calls if c[0] == 'PATCH']


class CreationRattacheeTests(Base):

    def test_creation_avec_parent_conteneur_cree_au_besoin(self):
        resp = self.as_(ALICE).post('/api/trees/', {'name': 'A'}, format='json')
        self.assertEqual(resp.status_code, 201)
        espace = f"arbre-genealogique-tree-{resp.json()['id']}"
        self.assertEqual(self.storage.parents, {C: None, espace: C})
        self.assertEqual(self.storage.members[espace], {ALICE: 'write'})

    def test_conteneur_existant_et_memorise_par_processus(self):
        self.storage.members[C] = {}
        for nom in ('A', 'B'):
            self.as_(ALICE).post('/api/trees/', {'name': nom}, format='json')
        sondes = [c for c in self.storage.calls if c == ('GET', f'http://storage.test/api/shares/{C}/members/')]
        self.assertEqual(len(sondes), 1)
        self.assertEqual({n: p for n, p in self.storage.parents.items()},
                         {n: C for n in self.storage.members if n != C})

    def test_repli_sans_parent_sur_400_parent(self):
        self.storage.refuse_parent = True
        tree = Tree.objects.create(name='A', owner_email=ALICE)
        with self.assertLogs('api.storage_client', 'WARNING') as logs:
            storage_client.sync_tree_share(tree)
        self.assertIsNone(self.storage.parents[ns(tree)])
        self.assertEqual(self.storage.members[ns(tree)], {ALICE: 'write'})
        texte = ''.join(logs.output)
        self.assertNotIn('http', texte)
        self.assertNotIn('jeton', texte.lower())

    def test_repli_quand_le_conteneur_appartient_a_un_autre(self):
        self.storage.foreign.add(C)
        tree = Tree.objects.create(name='A', owner_email=ALICE)
        with self.assertLogs('api.storage_client', 'WARNING'):
            storage_client.sync_tree_share(tree)
        self.assertIsNone(self.storage.parents[ns(tree)])
        self.assertEqual(self.storage.members[ns(tree)], {ALICE: 'write'})
        self.assertFalse(storage_client._container_ready)     # réessayé la prochaine fois

    def test_enfant_deja_existant_400_name_est_un_succes(self):
        tree = Tree.objects.create(name='A', owner_email=ALICE)
        reel = storage_client.list_members
        sondes = []

        def list_members(nom):
            # Course : le partage apparaît entre la sonde et la création.
            if nom == ns(tree) and not sondes:
                sondes.append(nom)
                self.storage.members[nom] = {}
                raise storage_client.StorageClientError('absent')
            return reel(nom)
        with mock.patch.object(storage_client, 'list_members', side_effect=list_members):
            storage_client.sync_tree_share(tree)        # ne lève pas
        self.assertEqual(self.storage.members[ns(tree)], {ALICE: 'write'})

    def test_echec_du_rattachement_ne_fait_pas_echouer_creation_ni_depot(self):
        self.storage.refuse_parent = True
        self.storage.foreign.add(C)
        alice = self.as_(ALICE)
        with self.assertLogs('api.storage_client', 'WARNING'):
            resp = alice.post('/api/trees/', {'name': 'A'}, format='json')
        self.assertEqual(resp.status_code, 201)
        tree_id = resp.json()['id']
        resp = alice.post('/api/media/', {
            'tree': tree_id,
            'file': SimpleUploadedFile('p.png', b'\x89PNG', content_type='image/png'),
        }, format='multipart')
        self.assertEqual(resp.status_code, 201, resp.content)
        media = MediaObject.objects.get(pk=resp.json()['id'])
        self.assertEqual(self.storage.files[f'arbre-genealogique-tree-{tree_id}'][media.storage_path],
                         b'\x89PNG')


class CommandeRattachementTests(Base):

    def setUp(self):
        super().setUp()
        self.t2 = Tree.objects.create(name='2', owner_email=ALICE)
        self.t19 = Tree.objects.create(name='19', owner_email=BOB)
        self.range = Tree.objects.create(name='rangé', owner_email=ALICE)
        self.jamais = Tree.objects.create(name='jamais synchronisé', owner_email=ALICE)
        for t in (self.t2, self.t19):
            self.storage.members[ns(t)] = {}
        self.storage.members[C] = {}
        self.storage.members[ns(self.range)] = {}
        self.storage.parents[ns(self.range)] = C
        self.storage.members['arbre-genealogique-tree-999'] = {}   # sans arbre : jamais touché

    def test_check_est_en_lecture_seule_et_sort_en_1(self):
        code, sortie = self.commande('--check')
        self.assertEqual(code, 1)
        self.assertIn(ns(self.t2), sortie)
        self.assertIn(ns(self.t19), sortie)
        self.assertNotIn(ns(self.range), sortie)
        self.assertEqual(self.storage.writes(), [])
        self.assertNotIn('http', sortie)

    def test_rattache_puis_rien_a_faire_au_second_passage(self):
        self.assertEqual(self.commande()[0], 0)
        self.assertEqual(self.storage.parents[ns(self.t2)], C)
        self.assertEqual(self.storage.parents[ns(self.t19)], C)
        self.assertEqual(len(self.patches()), 2)
        self.assertNotIn(ns(self.jamais), self.storage.members)          # ignoré, pas créé
        self.assertIsNone(self.storage.parents.get('arbre-genealogique-tree-999'))
        self.assertEqual(self.commande('--check')[0], 0)
        self.assertEqual(self.commande()[0], 0)
        self.assertEqual(len(self.patches()), 2)

    def test_cree_le_conteneur_s_il_manque(self):
        del self.storage.members[C]
        self.assertEqual(self.commande('--check')[0], 1)
        self.assertEqual(self.commande()[0], 0)
        self.assertIn(C, self.storage.members)
        self.assertEqual(self.storage.parents[ns(self.t2)], C)

    def test_conteneur_a_un_autre_code_1_rien_touche(self):
        del self.storage.members[C]
        self.storage.foreign.add(C)
        self.assertEqual(self.commande()[0], 1)
        self.assertEqual(self.patches(), [])

    def test_patch_refuse_compte_en_echec_et_continue(self):
        self.storage.refuse_parent = True
        code, sortie = self.commande()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.patches()), 2)
        self.assertIn('2 échec', sortie)

    def test_patch_404_ignore(self):
        reel = self.storage.patch

        def patch(url, **kw):
            if url.endswith(f'/api/shares/{ns(self.t2)}/'):
                self.storage.members.pop(ns(self.t2), None)    # disparu entre lecture et PATCH
            return reel(url, **kw)
        with mock.patch.object(self.storage, 'patch', side_effect=patch):
            self.assertEqual(self.commande()[0], 0)
        self.assertEqual(self.storage.parents[ns(self.t19)], C)

    def test_storage_injoignable_code_1(self):
        self.storage.down = True
        self.assertEqual(self.commande('--check')[0], 1)
        self.assertEqual(self.commande()[0], 1)
