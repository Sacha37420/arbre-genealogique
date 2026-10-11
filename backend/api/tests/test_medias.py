"""Médias — les octets vivent dans ``storage``, un espace par arbre.

Ce qui est vérifié ici : où les octets sont déposés, par quel compte (le compte
de service, jamais le jeton de l'utilisateur), qu'ils disparaissent avec leur
fiche quel que soit le chemin de suppression (signal ``post_delete``), quelle URL
est rendue au navigateur, et que les membres du partage ``storage`` suivent
``TreeShare`` — c'est ce qui rend sûr l'accès direct navigateur → storage.
"""
import base64
import hashlib
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from api import storage_client
from api.models import Individual, MediaLink, MediaObject, Tree, TreeShare
from api.providers import PersonResult

from .helpers import ALICE, BOB, CAROL, DAVE, FamilyTreeCase, LabTestCase, _Response, ns

PNG = b'\x89PNG-octets'


def fichier(nom='photo.png'):
    return SimpleUploadedFile(nom, PNG, content_type='image/png')


class DepotTests(FamilyTreeCase):

    def test_envoi_de_fichier_par_le_proprietaire(self):
        resp = self.alice.post('/api/media/', {'tree': self.tree.pk, 'file': fichier()},
                               format='multipart')
        self.assertEqual(resp.status_code, 201, resp.content)
        media = MediaObject.objects.get(pk=resp.json()['id'])
        self.assertEqual(self.storage.files[ns(self.tree)][media.storage_path], PNG)
        self.assertRegex(media.storage_path, r'^media/[0-9a-f]{32}-photo\.png$')
        self.assertEqual((media.size, media.mime, media.filename), (len(PNG), 'image/png', 'photo.png'))
        self.assertEqual(media.checksum, hashlib.sha256(PNG).hexdigest())
        # Rien n'est déposé ailleurs que dans l'espace de CET arbre.
        self.assertNotIn(media.storage_path, self.storage.files[ns(self.other)])
        # Les octets ne reviennent jamais dans le JSON.
        self.assertNotIn('data_base64', resp.json())
        self.assertNotIn('storage_path', resp.json())

    def test_storage_ne_voit_que_le_compte_de_service(self):
        self.alice.post('/api/media/', {'tree': self.tree.pk, 'file': fichier()}, format='multipart')
        self.alice.get(f"/api/media/{self.objects['media'].pk}/file/")
        self.assertTrue(self.storage.authorizations)
        self.assertEqual(set(self.storage.authorizations), {'Bearer jeton-de-service'})
        # Le jeton de service est demandé une fois, puis gardé en mémoire.
        self.assertEqual(self.storage.token_requests, 1)

    def test_la_photo_envoyee_depuis_une_carte_devient_le_portrait(self):
        pere = self.objects['individuals']
        ancien = self.objects['media-links']
        resp = self.alice.post('/api/media/', {
            'tree': self.tree.pk, 'file': fichier(), 'individual': pere.pk}, format='multipart')
        nouveau = MediaLink.objects.get(media_id=resp.json()['id'])
        ancien.refresh_from_db()
        self.assertTrue(nouveau.is_primary)
        self.assertFalse(ancien.is_primary)                      # déclassé, pas supprimé
        self.assertEqual(MediaLink.objects.filter(individual=pere, is_primary=True).count(), 1)

    def test_la_photo_ne_se_rattache_pas_a_une_personne_d_un_autre_arbre(self):
        resp = self.alice.post('/api/media/', {
            'tree': self.tree.pk, 'file': fichier(),
            'individual': self.other_objects['individuals'].pk}, format='multipart')
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(MediaLink.objects.filter(media_id=resp.json()['id']).exists())
        self.assertTrue(self.other_objects['media-links'].__class__.objects
                        .get(pk=self.other_objects['media-links'].pk).is_primary)

    def test_envoi_en_base64_par_un_editeur(self):
        corps = {'tree': self.tree.pk, 'title': 'Scan', 'filename': '../../acte de naissance.png',
                 'data_base64': 'data:image/png;base64,' + base64.b64encode(PNG).decode()}
        resp = self.bob.post('/api/media/', corps, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        media = MediaObject.objects.get(pk=resp.json()['id'])
        self.assertEqual(media.mime, 'image/png')                  # lu dans l'en-tête data:
        self.assertEqual(self.storage.files[ns(self.tree)][media.storage_path], PNG)
        self.assertEqual(media.size, len(PNG))
        # Le nom fourni par le client ne peut pas sortir du dossier « media/ ».
        self.assertRegex(media.storage_path, r'^media/[0-9a-f]{32}-[A-Za-z0-9._-]+$')
        self.assertEqual(media.storage_path.count('/'), 1)

    def test_nom_de_fichier_rendu_inoffensif(self):
        # Un seul segment de chemin, jamais vide, jamais « . » ni « .. ».
        for nom, attendu in (('../../etc/passwd', '_.._etc_passwd'), ('..', 'media'),
                             ('', 'media'), ('.', 'media'), ('a b/c.png', 'a_b_c.png'),
                             ('.cache.', 'cache'), ('été.jpg', '_t_.jpg'), ('a\\b', 'a_b')):
            with self.subTest(nom=nom):
                propre = storage_client._safe_name(nom)
                self.assertEqual(propre, attendu)
                self.assertNotIn('/', propre)
                self.assertNotIn(propre, ('', '.', '..'))

    def test_storage_en_panne_aucune_fiche_fantome(self):
        self.storage.down = True
        avant = MediaObject.objects.count()
        resp = self.alice.post('/api/media/', {'tree': self.tree.pk, 'file': fichier()},
                               format='multipart')
        self.assertEqual(resp.status_code, 502)
        resp = self.alice.post('/api/media/', {'tree': self.tree.pk, 'data_base64': 'QUJD'},
                               format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(MediaObject.objects.count(), avant)

    def test_remplacer_le_contenu_purge_l_ancien(self):
        media = self.objects['media']
        ancien = media.storage_path
        resp = self.bob.patch(f'/api/media/{media.pk}/', {'data_base64': 'Tk9VVkVBVQ=='},
                              format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        media.refresh_from_db()
        self.assertNotEqual(media.storage_path, ancien)
        self.assertEqual(self.storage.files[ns(self.tree)], {media.storage_path: b'NOUVEAU'})
        self.assertIn('portrait.jpg', media.storage_path)        # nom repris de la fiche

    def test_modifier_la_fiche_sans_contenu_ne_touche_pas_storage(self):
        media = self.objects['media']
        self.bob.patch(f'/api/media/{media.pk}/', {'title': 'Nouveau titre'}, format='json')
        self.assertEqual(self.storage.writes(), [])


class PurgeTests(FamilyTreeCase):
    """Un média disparaît par quatre chemins ; les octets partent à chaque fois."""

    def blobs(self, tree=None):
        return self.storage.files[ns(tree or self.tree)]

    def test_suppression_de_la_fiche(self):
        self.assertEqual(self.bob.delete(f"/api/media/{self.objects['media'].pk}/").status_code, 204)
        self.assertEqual(self.blobs(), {})
        # … et seulement les siens : même chemin logique dans les autres arbres.
        self.assertEqual(self.blobs(self.other), {'media/abc-portrait.jpg': b'JPEG'})
        self.assertEqual(self.blobs(self.public), {'media/abc-portrait.jpg': b'JPEG'})

    def test_suppression_du_dernier_lien(self):
        lien, media = self.objects['media-links'], self.objects['media']
        self.assertEqual(self.bob.delete(f'/api/media-links/{lien.pk}/').status_code, 204)
        self.assertFalse(MediaObject.objects.filter(pk=media.pk).exists())
        self.assertEqual(self.blobs(), {})

    def test_un_media_encore_lie_est_garde(self):
        lien, media = self.objects['media-links'], self.objects['media']
        MediaLink.objects.create(media=media, individual=self.objects['_mere'])
        self.assertEqual(self.bob.delete(f'/api/media-links/{lien.pk}/').status_code, 204)
        self.assertTrue(MediaObject.objects.filter(pk=media.pk).exists())
        self.assertEqual(len(self.blobs()), 1)

    def test_suppression_de_l_arbre_cascade(self):
        MediaObject.objects.create(tree=self.tree, storage_path='media/second.png')
        self.blobs()['media/second.png'] = b'2'
        self.assertEqual(self.alice.delete(f'/api/trees/{self.tree.pk}/').status_code, 204)
        self.assertEqual(self.blobs(), {})
        self.assertEqual(len(self.blobs(self.other)), 1)

    def test_suppression_hors_de_toute_vue(self):
        # Le signal, pas une vue : un .delete() ORM purge aussi.
        Tree.objects.filter(pk=self.tree.pk).delete()
        self.assertEqual(self.blobs(), {})

    def test_media_externe_rien_a_purger(self):
        externe = MediaObject.objects.create(tree=self.tree, external_url='https://example.org/a.jpg')
        self.assertEqual(self.alice.delete(f'/api/media/{externe.pk}/').status_code, 204)
        self.assertEqual(self.storage.calls, [])

    def test_purge_impossible_la_suppression_aboutit_quand_meme(self):
        self.storage.down = True
        with self.assertLogs('api.models', level='WARNING') as journal:
            resp = self.alice.delete(f"/api/media/{self.objects['media'].pk}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(MediaObject.objects.filter(pk=self.objects['media'].pk).exists())
        self.assertIn('Purge storage échouée', journal.output[0])

    def test_supprimer_le_portrait_en_promeut_un_autre(self):
        pere, media = self.objects['individuals'], self.objects['media']
        autre = MediaObject.objects.create(tree=self.tree, external_url='https://example.org/b.jpg')
        second = MediaLink.objects.create(media=autre, individual=pere, is_primary=False)
        self.bob.delete(f"/api/media-links/{self.objects['media-links'].pk}/")
        second.refresh_from_db()
        self.assertTrue(second.is_primary)

    def test_choisir_un_portrait_declasse_l_ancien(self):
        pere = self.objects['individuals']
        autre = MediaObject.objects.create(tree=self.tree, external_url='https://example.org/b.jpg')
        second = MediaLink.objects.create(media=autre, individual=pere, is_primary=False)
        resp = self.bob.patch(f'/api/media-links/{second.pk}/', {'is_primary': True}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(MediaLink.objects.filter(individual=pere, is_primary=True)
                              .values_list('pk', flat=True)), [second.pk])
        # Le portrait d'une AUTRE personne n'est pas touché.
        self.assertTrue(MediaLink.objects.get(pk=self.other_objects['media-links'].pk).is_primary)


class UrlTests(FamilyTreeCase):
    """Quelle adresse le navigateur reçoit pour lire une photo."""

    def url(self, client, objs):
        return client.get(f"/api/media/{objs['media'].pk}/").json()['file_url']

    def test_arbre_prive_adresse_directe_dans_son_espace(self):
        self.assertEqual(
            self.url(self.alice, self.objects),
            f'https://lab.example.org/storage-api/api/files/{ns(self.tree)}/content/media/abc-portrait.jpg',
        )

    def test_arbre_public_toujours_par_le_proxy(self):
        # storage ne sait pas dire « tout compte de l'app » : c'est l'app qui sert.
        self.assertEqual(self.url(self.dave, self.public_objects),
                         f"/api/media/{self.public_objects['media'].pk}/file/")

    @override_settings(PUBLIC_DOMAIN='CHANGE_ME')
    def test_sans_domaine_repli_sur_le_proxy(self):
        self.assertEqual(self.url(self.alice, self.objects),
                         f"/api/media/{self.objects['media'].pk}/file/")

    def test_media_externe_son_adresse_telle_quelle(self):
        externe = MediaObject.objects.create(tree=self.tree, external_url='https://example.org/a.jpg')
        self.assertEqual(storage_client.media_url(externe), 'https://example.org/a.jpg')

    def test_la_meme_adresse_partout(self):
        pere = self.objects['individuals']
        attendu = self.url(self.alice, self.objects)
        fiche = self.alice.get(f'/api/individuals/{pere.pk}/').json()
        carte = next(n for n in self.alice.get(f'/api/trees/{self.tree.pk}/graph/').json()['nodes']
                     if n['id'] == pere.pk)
        self.assertEqual(fiche['photo_url'], attendu)
        self.assertEqual(fiche['media_links'][0]['file_url'], attendu)
        self.assertEqual(carte['photo_url'], attendu)
        self.assertTrue(carte['has_photo'])

    def test_nom_de_l_espace_par_arbre(self):
        self.assertEqual(ns(self.tree), f'arbre-genealogique-tree-{self.tree.pk}')
        self.assertNotEqual(ns(self.tree), ns(self.other))


class SynchronisationDesMembresTests(LabTestCase):
    """``TreeShare`` répliqué en membres du partage storage de l'arbre."""

    def setUp(self):
        super().setUp()
        self.alice = self.as_(ALICE)
        self.tree_id = self.alice.post('/api/trees/', {'name': 'A'}, format='json').json()['id']
        self.espace = f'arbre-genealogique-tree-{self.tree_id}'

    def membres(self):
        return self.storage.members[self.espace]

    def inviter(self, email, role):
        return self.alice.post('/api/tree-shares/', {
            'tree': self.tree_id, 'email': email, 'role': role}, format='json')

    def test_un_arbre_neuf_a_son_espace_et_son_proprietaire_en_ecriture(self):
        self.assertEqual(self.membres(), {ALICE: 'write'})

    def test_les_roles_suivent_les_partages(self):
        self.inviter(BOB, 'EDITOR')
        carol = self.inviter(CAROL, 'VIEWER').json()
        self.assertEqual(self.membres(), {ALICE: 'write', BOB: 'write', CAROL: 'read'})
        self.inviter(BOB, 'VIEWER')                                   # rétrogradé
        self.assertEqual(self.membres()[BOB], 'read')
        self.alice.patch(f"/api/tree-shares/{carol['id']}/", {'role': 'EDITOR'}, format='json')
        self.assertEqual(self.membres()[CAROL], 'write')
        self.alice.delete(f"/api/tree-shares/{carol['id']}/")
        self.assertEqual(self.membres(), {ALICE: 'write', BOB: 'read'})   # retiré de storage aussi

    def test_une_derive_est_corrigee_au_passage(self):
        # Un membre resté dans storage sans TreeShare (synchronisation partielle
        # passée) perd son accès à la synchronisation suivante.
        self.membres()[DAVE] = 'write'
        self.inviter(BOB, 'VIEWER')
        self.assertEqual(self.membres(), {ALICE: 'write', BOB: 'read'})

    def test_storage_en_panne_l_arbre_est_cree_quand_meme(self):
        self.storage.down = True
        with self.assertLogs('api.views', level='WARNING'):
            resp = self.alice.post('/api/trees/', {'name': 'B'}, format='json')
        self.assertEqual(resp.status_code, 201)
        # … et l'espace est rattrapé au premier partage.
        self.storage.down = False
        self.alice.post('/api/tree-shares/', {'tree': resp.json()['id'], 'email': BOB},
                        format='json')
        self.assertEqual(self.storage.members[f"arbre-genealogique-tree-{resp.json()['id']}"],
                         {ALICE: 'write', BOB: 'read'})

    @override_settings(KEYCLOAK_ADMIN_CLIENT_SECRET='')
    def test_sans_secret_de_service_rien_ne_part(self):
        storage_client._token_cache.update(value='', exp=0.0)      # jeton en mémoire oublié
        avant = len(self.storage.calls)
        with self.assertRaises(storage_client.StorageClientError):
            storage_client.sync_tree_share(Tree.objects.get(pk=self.tree_id))
        self.assertEqual(len(self.storage.calls), avant)


class PhotoImporteeTests(FamilyTreeCase):
    """Troisième chemin d'écriture : le portrait rapatrié depuis un fournisseur."""

    def setUp(self):
        super().setUp()
        resultat = PersonResult(provider='wikitree', external_id='C-1', given_name='Sam',
                                surname='Clemens', photo_url='https://example.org/img/sam.jpg')
        fournisseur = mock.Mock()
        fournisseur.fetch.return_value = resultat
        self._patch(mock.patch('api.views.get_provider', return_value=fournisseur))

    def importer(self, **extra):
        return self.alice.post('/api/enrich/import/', {
            'provider': 'wikitree', 'external_id': 'C-1', 'tree': self.tree.pk, **extra},
            format='json')

    def test_la_photo_est_deposee_dans_l_espace_de_l_arbre(self):
        reponse = _Response(200, content=b'PHOTO')
        reponse.headers = {'Content-Type': 'image/jpeg; charset=binary'}
        with mock.patch('api.views.requests.get', return_value=reponse) as get:
            resp = self.importer()
            self.assertEqual(resp.status_code, 201, resp.content)
            personne = Individual.objects.get(pk=resp.json()['individual']['id'])
            media = personne.media_links.get().media
            self.assertEqual(self.storage.files[ns(self.tree)][media.storage_path], b'PHOTO')
            self.assertEqual((media.mime, media.provider), ('image/jpeg', 'wikitree'))
            self.assertTrue(personne.media_links.get().is_primary)
            # Réimporter la même photo ne la dépose pas une seconde fois.
            avant = len(self.storage.files[ns(self.tree)])
            self.importer(target=personne.pk)
            self.assertEqual(len(self.storage.files[ns(self.tree)]), avant)
            self.assertEqual(personne.media_links.count(), 1)
        self.assertEqual(get.call_args[0][0], 'https://example.org/img/sam.jpg')

    def test_photo_injoignable_l_import_aboutit(self):
        # Aucun mock : le filet réseau des tests répond « connexion impossible ».
        resp = self.importer()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.network.call_count, 1)
        self.assertFalse(MediaLink.objects.filter(
            individual_id=resp.json()['individual']['id']).exists())

    def test_sans_photo_sur_demande(self):
        resp = self.importer(with_photo=False)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.network.call_count, 0)
