"""Taille maximale des médias (Lot 3) — 413 avant tout appel à ``storage``.

Pour chacun des trois chemins d'écriture (multipart, base64, portrait importé
d'un fournisseur) : la taille limite passe ; un octet de plus donne 413, sans
aucun appel émis vers storage (ni dépôt, ni même demande de jeton de service)
et sans fiche créée. La plupart des cas tournent sous une limite réduite
(``MEDIA_MAX_BYTES=1000``) ; la valeur réelle (50 Mo) est éprouvée sur le
chemin multipart.
"""
import base64
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from api import limits
from api.models import Individual, MediaObject
from api.providers import PersonResult

from .helpers import FamilyTreeCase, _Response, ns

LIMITE = 1000


def fichier(taille: int, nom='photo.png'):
    return SimpleUploadedFile(nom, b'x' * taille, content_type='image/png')


class _Base(FamilyTreeCase):

    def setUp(self):
        super().setUp()
        self.medias_avant = MediaObject.objects.count()

    def assertRefuseSansStorage(self, resp):
        self.assertEqual(resp.status_code, 413, resp.content)
        self.assertEqual(int(resp.json()['max_bytes']), limits.media_max_bytes())
        self.assertEqual(self.storage.calls, [])          # rien émis, pas même le jeton
        self.assertEqual(MediaObject.objects.count(), self.medias_avant)


class ValeurParDefautTests(_Base):

    def test_50_mo_par_defaut(self):
        # Le .env n'a pas la clé (contrat urgences v1, § 7) : le défaut s'applique.
        self.assertEqual(settings.MEDIA_MAX_BYTES, 52428800)

    def test_django_laisse_passer_un_base64_a_la_limite(self):
        # Un base64 à la limite, préfixe data: compris, tient sous la limite de
        # corps de Django (sinon 400 avant même notre contrôle).
        plus_gros = limits.base64_length(settings.MEDIA_MAX_BYTES) + len('data:image/png;base64,')
        self.assertGreater(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, plus_gros)

    def test_multipart_a_50_mo_accepte(self):
        resp = self.alice.post('/api/media/', {
            'tree': self.tree.pk, 'file': fichier(52428800)}, format='multipart')
        self.assertEqual(resp.status_code, 201, resp.content[:300])
        media = MediaObject.objects.get(pk=resp.json()['id'])
        self.assertEqual(media.size, 52428800)
        self.assertEqual(len(self.storage.files[ns(self.tree)][media.storage_path]), 52428800)

    def test_multipart_a_50_mo_plus_un_octet_refuse(self):
        resp = self.alice.post('/api/media/', {
            'tree': self.tree.pk, 'file': fichier(52428801)}, format='multipart')
        self.assertRefuseSansStorage(resp)


@override_settings(MEDIA_MAX_BYTES=LIMITE)
class MultipartTests(_Base):

    def test_taille_limite_acceptee(self):
        resp = self.alice.post('/api/media/', {
            'tree': self.tree.pk, 'file': fichier(LIMITE)}, format='multipart')
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(MediaObject.objects.get(pk=resp.json()['id']).size, LIMITE)

    def test_un_octet_de_plus_refuse(self):
        resp = self.alice.post('/api/media/', {
            'tree': self.tree.pk, 'file': fichier(LIMITE + 1)}, format='multipart')
        self.assertRefuseSansStorage(resp)

    def test_corps_annonce_trop_gros_refuse_sans_etre_lu(self):
        # Content-Length au-delà du plus gros envoi légitime : refus avant lecture.
        with mock.patch('rest_framework.parsers.DjangoMultiPartParser') as parseur:
            resp = self.alice.post(
                '/api/media/', {'tree': self.tree.pk, 'file': fichier(10)}, format='multipart',
                CONTENT_LENGTH=str(limits.body_max_bytes() + 1))
        self.assertRefuseSansStorage(resp)
        parseur.assert_not_called()


@override_settings(MEDIA_MAX_BYTES=LIMITE)
class Base64Tests(_Base):

    def envoyer(self, octets: bytes, prefixe='data:image/png;base64,', client=None):
        client = client or self.alice
        return client.post('/api/media/', {
            'tree': self.tree.pk, 'filename': 'scan.png',
            'data_base64': prefixe + base64.b64encode(octets).decode()}, format='json')

    def test_taille_limite_acceptee(self):
        for taille in (LIMITE, LIMITE - 1, LIMITE - 2):        # trois longueurs de remplissage
            with self.subTest(taille=taille):
                resp = self.envoyer(b'y' * taille)
                self.assertEqual(resp.status_code, 201, resp.content)
                self.assertEqual(MediaObject.objects.get(pk=resp.json()['id']).size, taille)

    def test_un_octet_de_plus_refuse(self):
        for prefixe in ('data:image/png;base64,', ''):
            with self.subTest(prefixe=prefixe):
                self.assertRefuseSansStorage(self.envoyer(b'y' * (LIMITE + 1), prefixe))

    def test_taille_decodee_verifiee_avant_decodage(self):
        with mock.patch.object(limits, 'base64') as module_base64:
            resp = self.envoyer(b'y' * (LIMITE + 1))
        self.assertRefuseSansStorage(resp)
        module_base64.b64decode.assert_not_called()

    def test_retours_a_la_ligne_ignores(self):
        brut = base64.encodebytes(b'y' * LIMITE).decode()     # MIME : une ligne / 76 car.
        self.assertIn('\n', brut)
        resp = self.alice.post('/api/media/', {
            'tree': self.tree.pk, 'filename': 'scan.png', 'data_base64': brut}, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(MediaObject.objects.get(pk=resp.json()['id']).size, LIMITE)

    def test_remplacement_par_patch_aussi_borne(self):
        media = self.objects['media']
        resp = self.bob.patch(f'/api/media/{media.pk}/', {
            'data_base64': base64.b64encode(b'z' * (LIMITE + 1)).decode()}, format='json')
        self.assertRefuseSansStorage(resp)
        media.refresh_from_db()
        self.assertEqual(media.storage_path, 'media/abc-portrait.jpg')

    def test_base64_invalide_400_sans_storage(self):
        resp = self.alice.post('/api/media/', {
            'tree': self.tree.pk, 'data_base64': 'pas du base64 !'}, format='json')
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self.storage.calls, [])

    def test_base64_dans_un_formulaire_multipart(self):
        # Même champ envoyé en multipart sans « file » : même contrôle.
        resp = self.alice.post('/api/media/', {
            'tree': self.tree.pk, 'filename': 'scan.png',
            'data_base64': base64.b64encode(b'y' * (LIMITE + 1)).decode()}, format='multipart')
        self.assertRefuseSansStorage(resp)


@override_settings(MEDIA_MAX_BYTES=LIMITE)
class ImportFournisseurTests(_Base):

    def setUp(self):
        super().setUp()
        resultat = PersonResult(provider='wikitree', external_id='C-1', given_name='Sam',
                                surname='Clemens', photo_url='https://example.org/img/sam.jpg')
        fournisseur = mock.Mock()
        fournisseur.fetch.return_value = resultat
        self._patch(mock.patch('api.views.get_provider', return_value=fournisseur))
        self.individus_avant = Individual.objects.count()

    def importer(self, reponse):
        with mock.patch('api.views.requests.get', return_value=reponse):
            return self.alice.post('/api/enrich/import/', {
                'provider': 'wikitree', 'external_id': 'C-1', 'tree': self.tree.pk},
                format='json')

    @staticmethod
    def photo(octets: bytes, annonce: int | None = None):
        reponse = _Response(200, content=octets)
        reponse.headers = {'Content-Type': 'image/jpeg'}
        if annonce is not None:
            reponse.headers['Content-Length'] = str(annonce)
        return reponse

    def assertImportRefuse(self, resp):
        self.assertRefuseSansStorage(resp)
        # L'import entier est annulé (transaction) : aucune personne créée.
        self.assertEqual(Individual.objects.count(), self.individus_avant)

    def test_taille_limite_acceptee(self):
        for annonce in (LIMITE, None):
            with self.subTest(annonce=annonce):
                resp = self.importer(self.photo(b'p' * LIMITE, annonce))
                self.assertEqual(resp.status_code, 201, resp.content)
                personne = Individual.objects.get(pk=resp.json()['individual']['id'])
                self.assertEqual(personne.media_links.get().media.size, LIMITE)
                MediaObject.objects.filter(size=LIMITE).delete()   # pas de déduplication au tour suivant

    def test_taille_annoncee_trop_grande_refusee_sans_lire(self):
        reponse = self.photo(b'', annonce=LIMITE + 1)
        reponse.iter_content = mock.Mock(side_effect=AssertionError('corps lu'))
        self.assertImportRefuse(self.importer(reponse))

    def test_taille_inconnue_un_octet_de_plus_refuse(self):
        self.assertImportRefuse(self.importer(self.photo(b'p' * (LIMITE + 1))))

    def test_taille_annoncee_mensongere_refusee(self):
        self.assertImportRefuse(self.importer(self.photo(b'p' * (LIMITE + 1), annonce=10)))


@override_settings(MEDIA_MAX_BYTES=LIMITE)
class DernierFiletTests(_Base):

    def test_upload_bytes_refuse_sans_appel(self):
        from api import storage_client
        with self.assertRaises(limits.MediaTooLarge):
            storage_client.upload_bytes(b'x' * (LIMITE + 1), 'a.png', namespace=ns(self.tree))
        self.assertEqual(self.storage.calls, [])
