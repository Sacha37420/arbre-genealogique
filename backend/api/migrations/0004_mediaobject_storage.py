"""MediaObject.data (BinaryField dans devdb) → MediaObject.storage_path
(chemin dans le partage storage de l'app).

Même schéma que `traitement-de-fichiers-compils` (migration 0004) : le transfert
des octets et la suppression de la colonne vivent dans **la même migration**,
donc la même transaction. Si storage est injoignable ou refuse un upload, la
migration échoue et la colonne survit — le conteneur ne démarre pas (`migrate`
est dans le `CMD`), ce qui est le bon mode d'échec : mieux vaut une app arrêtée
qu'une app démarrée sans les photos de ses utilisateurs.

Prérequis, à faire AVANT de déployer (sinon la migration échoue proprement) :
  - le partage doit exister côté storage :
      python manage.py create_group_share arbre-genealogique \\
          --owner sacha --required-groups developers
  - le compte de service doit y être autorisé (storage/.env) :
      KEYCLOAK_SERVICE_WRITE_SHARES=…,arbre-genealogique-admin:arbre-genealogique
  - le client compagnon doit exister (setup2.sh le provisionne via
    .keycloak-service-account-roles) et son secret être dans .env.

Le chemin cible est dérivé de la clé primaire (`media/<pk>-<nom>`), pas d'un UUID
comme les uploads courants : une reprise après échec partiel réécrit au même
chemin plutôt que de dupliquer des blobs orphelins (storage écrase sur
(namespace, chemin)).

Les médias externes (external_url renseigné, data NULL) n'ont rien à transférer :
leur storage_path reste vide, ce qui est exactement le test utilisé partout dans
le code pour les distinguer.
"""
import io
import re

from django.db import migrations, models


def _safe_name(nom: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', (nom or '').strip()) or 'media'
    return cleaned.strip('.') or 'media'


def blobs_vers_storage(apps, schema_editor):
    from api import storage_client

    MediaObject = apps.get_model('api', 'MediaObject')
    for media in MediaObject.objects.all().iterator():
        octets = bytes(media.data or b'')
        if not octets:
            # Média externe (ou fiche sans contenu) : rien à transférer.
            continue
        nom = _safe_name(media.filename or media.title or f'media-{media.pk}')
        result = storage_client.upload(
            f'media/{media.pk}-{nom}',
            io.BytesIO(octets),
            nom,
            media.mime or 'application/octet-stream',
        )
        media.storage_path = result['relative_path']
        media.save(update_fields=['storage_path'])


def storage_vers_blobs(apps, schema_editor):
    """Marche arrière : rapatrie les octets depuis storage dans la colonne.

    Ne supprime rien de storage — un rollback est déjà une situation anormale,
    mieux vaut y laisser des blobs orphelins (repérables par leur chemin
    `media/<pk>-…`) que de détruire l'unique copie si la migration inverse
    échouait ensuite.
    """
    from api import storage_client

    MediaObject = apps.get_model('api', 'MediaObject')
    for media in MediaObject.objects.exclude(storage_path='').iterator():
        resp = storage_client.download(media.storage_path)
        media.data = b''.join(resp.iter_content(65536))
        media.save(update_fields=['data'])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0003_treeshare'),
    ]

    operations = [
        migrations.AddField(
            model_name='mediaobject',
            name='storage_path',
            field=models.CharField(blank=True, default='', max_length=1024),
        ),
        migrations.RunPython(blobs_vers_storage, storage_vers_blobs),
        migrations.RemoveField(
            model_name='mediaobject',
            name='data',
        ),
    ]
