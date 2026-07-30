"""Commande à usage unique — synchronisation Share/ShareMember par arbre.

Deux étapes, pour chaque `Tree` :

1. `storage_client.sync_tree_share(tree)` — crée (ou met à jour) le partage
   storage `arbre-genealogique-tree-<id>` et ses `ShareMember` (propriétaire +
   `TreeShare`). Idempotent : peut être relancée sans risque.

2. Pour chaque `MediaObject` de cet arbre dont `storage_path` existe encore
   sous l'ancien partage global (`settings.STORAGE_NAMESPACE`, cf. l'historique
   de la première migration vers storage, avant que le partage soit par arbre) :
   télécharge le blob depuis l'ancien namespace, le redépose SOUS LE MÊME
   `relative_path` dans le nouveau namespace par arbre, vérifie le checksum
   sha256 contre `MediaObject.checksum`, puis seulement alors supprime le blob
   de l'ancien namespace. `MediaObject.storage_path` ne change pas de valeur —
   seul le namespace change, et celui-ci n'est jamais stocké en base : il se
   retrouve à partir de `tree_id` (cf. storage_client.namespace_for_tree). Zéro
   écriture en base par cette commande, donc rien à modéliser en migration
   Django : uniquement des octets qui changent de partage côté storage.

Une déjà-migrée (storage_path absent du vieux namespace) est simplement
ignorée : la commande peut être relancée sans dupliquer de transfert.

À supprimer une fois vérifiée en production (même politique que
`transferer_vers_storage` pour atelier-3d, cf. CLAUDE.md racine) : inutile
une fois qu'il n'existe plus de média sous l'ancien partage global.

    python manage.py migrer_medias_vers_partages_par_arbre [--dry-run]
"""
import hashlib
import io

from django.conf import settings
from django.core.management.base import BaseCommand

from api import storage_client
from api.models import MediaObject, Tree


class Command(BaseCommand):
    help = (
        "Synchronise Share/ShareMember par arbre côté storage, et transfère les "
        "médias encore sous l'ancien partage global vers le partage de leur arbre."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="N'écrit ni ne transfère rien : affiche seulement ce qui serait fait.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        legacy_namespace = settings.STORAGE_NAMESPACE

        for tree in Tree.objects.all():
            self.stdout.write(f"— Arbre #{tree.pk} « {tree.name} » "
                               f"({'public' if tree.is_public else 'privé'})")

            if dry_run:
                self.stdout.write(f"  [dry-run] sync_tree_share (partage "
                                   f"'{storage_client.namespace_for_tree(tree.pk)}')")
            else:
                try:
                    storage_client.sync_tree_share(tree)
                except storage_client.StorageClientError as exc:
                    self.stderr.write(self.style.ERROR(
                        f"  échec sync_tree_share pour l'arbre #{tree.pk} : {exc}"
                    ))
                    continue
                self.stdout.write(self.style.SUCCESS(
                    f"  partage '{storage_client.namespace_for_tree(tree.pk)}' synchronisé "
                    f"({1 + tree.shares.count()} membre(s))."
                ))

            new_namespace = storage_client.namespace_for_tree(tree.pk)
            for media in tree.media.exclude(storage_path=''):
                self._migrate_one(media, legacy_namespace, new_namespace, dry_run)

    def _migrate_one(self, media: MediaObject, legacy_namespace: str,
                      new_namespace: str, dry_run: bool) -> None:
        try:
            upstream = storage_client.download(media.storage_path, namespace=legacy_namespace)
        except storage_client.StorageClientError:
            # Déjà absent de l'ancien namespace : soit déjà migré par un
            # précédent passage de cette commande, soit un média qui n'a
            # jamais vécu sous l'ancien partage global (créé après le
            # basculement vers les partages par arbre). Rien à faire.
            self.stdout.write(f"    média #{media.pk} ({media.filename}) : "
                               f"déjà absent de '{legacy_namespace}', ignoré.")
            return

        blob = upstream.content  # `download()` renvoie `stream=True` : `.content` consomme tout
        checksum = hashlib.sha256(blob).hexdigest()
        if media.checksum and checksum != media.checksum:
            self.stderr.write(self.style.ERROR(
                f"    média #{media.pk} : checksum inattendu après téléchargement "
                f"(attendu {media.checksum}, obtenu {checksum}) — transfert annulé."
            ))
            return

        if dry_run:
            self.stdout.write(f"    [dry-run] média #{media.pk} ({media.filename}, "
                               f"{len(blob)} octets) : '{legacy_namespace}' → '{new_namespace}'")
            return

        storage_client.upload(
            media.storage_path, io.BytesIO(blob), media.filename or 'media',
            media.mime, namespace=new_namespace,
        )

        # Re-télécharge depuis le NOUVEAU namespace et revérifie le checksum
        # avant de purger l'ancien : ne jamais supprimer le seul exemplaire
        # valide d'un blob avant d'avoir la preuve que la copie a réussi.
        try:
            verif = storage_client.download(media.storage_path, namespace=new_namespace)
            verif_checksum = hashlib.sha256(verif.content).hexdigest()
        except storage_client.StorageClientError as exc:
            self.stderr.write(self.style.ERROR(
                f"    média #{media.pk} : re-téléchargement de vérification échoué ({exc}) "
                "— ancien blob conservé, à rejouer."
            ))
            return
        if verif_checksum != checksum:
            self.stderr.write(self.style.ERROR(
                f"    média #{media.pk} : checksum divergent après upload — ancien blob "
                "conservé, à investiguer manuellement."
            ))
            return

        storage_client.delete(media.storage_path, namespace=legacy_namespace)
        self.stdout.write(self.style.SUCCESS(
            f"    média #{media.pk} ({media.filename}, {len(blob)} octets) : "
            f"'{legacy_namespace}' → '{new_namespace}', checksum vérifié."
        ))
