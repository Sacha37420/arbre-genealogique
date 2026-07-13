"""
Vues de l'API.

Toutes les ressources sont cloisonnées par arbre, et un arbre appartient à
l'utilisateur Keycloak qui l'a créé : `owner_email` est renseigné par le serveur
depuis le JWT, jamais par le client.
"""
import hashlib

import requests
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .auth_extra import KeycloakQueryTokenAuthentication
from .defaults import build_defaults
from .gedcom import GedcomExporter, GedcomImporter, category_for, parse_date
from .graph import build_graph, build_timeline
from .models import (
    CardTemplate,
    Citation,
    CustomFieldDef,
    EdgeStyle,
    EnrichmentMatch,
    Event,
    EventTag,
    Family,
    FamilyChild,
    FamilyLayout,
    FamilySpouse,
    ImportJob,
    Individual,
    MediaLink,
    MediaObject,
    NodeLayout,
    NodeStyle,
    PersonalName,
    Place,
    Repository,
    Sex,
    SharedNote,
    Source,
    SpouseRole,
    StyleRule,
    Tree,
    TreeViewSettings,
    UserRecord,
)
from .providers import Geocoder, ProviderError, SearchQuery, describe_all, get_provider
from .serializers import (
    CardTemplateSerializer,
    CitationSerializer,
    CustomFieldDefSerializer,
    EdgeStyleSerializer,
    EnrichmentMatchSerializer,
    EventSerializer,
    FamilyChildSerializer,
    FamilyLayoutSerializer,
    FamilySerializer,
    FamilySpouseSerializer,
    ImportJobSerializer,
    IndividualSerializer,
    IndividualWriteSerializer,
    LayoutBulkSerializer,
    MediaLinkSerializer,
    MediaObjectSerializer,
    NodeLayoutSerializer,
    NodeStyleSerializer,
    PersonalNameSerializer,
    PlaceSerializer,
    RepositorySerializer,
    SharedNoteSerializer,
    SourceSerializer,
    StyleRuleSerializer,
    TreeSerializer,
    TreeViewSettingsSerializer,
)


class MeView(APIView):
    """GET /api/me/ — identité de l'utilisateur, créée à la première visite."""

    def get(self, request):
        record, created = UserRecord.objects.get_or_create(
            email=request.user.email,
            defaults={'display_name': request.user.username},
        )
        return Response({
            'email': record.email,
            'username': request.user.username,
            'groups': request.user.claims.get('groups', []),
            'display_name': record.display_name,
            'registered_at': record.registered_at,
            'is_new': created,
            'tree_count': Tree.objects.filter(owner_email=record.email).count(),
        })


# ─────────────────────────────────────────────────────────────────────────────
# Cloisonnement par propriétaire
# ─────────────────────────────────────────────────────────────────────────────

class OwnedTreeMixin:
    """
    Restreint le queryset aux objets des arbres visibles par l'utilisateur.

    `tree_path` est le chemin ORM qui mène de l'objet à son arbre — c'est ce qui
    permet d'appliquer la même règle de propriété à toutes les ressources filles.
    """

    tree_path = 'tree'

    def visible_trees(self):
        return Tree.objects.filter(
            Q(owner_email=self.request.user.email) | Q(is_public=True)
        )

    def owned_trees(self):
        return Tree.objects.filter(owner_email=self.request.user.email)

    def get_queryset(self):
        qs = super().get_queryset()
        trees = self.visible_trees() if self.request.method in ('GET', 'HEAD') else self.owned_trees()
        qs = qs.filter(**{f'{self.tree_path}__in': trees})

        # ?tree=<id> — filtre systématique côté client, l'arbre courant.
        tree_id = self.request.query_params.get('tree')
        if tree_id:
            qs = qs.filter(**{self.tree_path: tree_id})
        return qs

    def check_tree(self, tree: Tree) -> None:
        if tree.owner_email != self.request.user.email:
            raise PermissionDenied("Cet arbre ne vous appartient pas.")

    def _tree_of(self, validated: dict) -> Tree | None:
        """
        Remonte de l'objet écrit jusqu'à son arbre, en suivant `tree_path`.

        Le filtrage par queryset ne protège que les objets existants : sans cette
        vérification, une création pourrait viser l'arbre d'un autre utilisateur,
        puisque `tree` (ou le parent) est fourni par le client.
        """
        parts = self.tree_path.split('__')
        obj = validated.get(parts[0])
        for part in parts[1:]:
            obj = getattr(obj, part, None)
        return obj if isinstance(obj, Tree) else None

    def perform_create(self, serializer):
        tree = self._tree_of(serializer.validated_data)
        if tree is not None:
            self.check_tree(tree)
        serializer.save()

    def perform_update(self, serializer):
        # Interdit aussi de *déplacer* un objet vers l'arbre d'autrui.
        tree = self._tree_of(serializer.validated_data)
        if tree is not None:
            self.check_tree(tree)
        serializer.save()


class TreeViewSet(viewsets.ModelViewSet):
    """CRUD des arbres, plus le graphe, la frise, l'import et l'export GEDCOM."""

    serializer_class = TreeSerializer
    queryset = Tree.objects.all()

    def get_queryset(self):
        if self.request.method in ('GET', 'HEAD'):
            return Tree.objects.filter(
                Q(owner_email=self.request.user.email) | Q(is_public=True)
            )
        return Tree.objects.filter(owner_email=self.request.user.email)

    def perform_create(self, serializer):
        tree = serializer.save(
            owner_email=self.request.user.email,
            submitter_name=self.request.user.username,
        )
        # Un arbre neuf doit être affichable immédiatement : styles, gabarits, réglages.
        build_defaults(tree)

    @extend_schema(
        summary='Graphe complet de l’arbre',
        description='Nœuds (cartes prêtes à afficher, styles résolus), jonctions '
                    'familiales, liens et gabarits. C’est la seule requête dont a '
                    'besoin la page principale.',
    )
    @action(detail=True, methods=['get'])
    def graph(self, request, pk=None):
        return Response(build_graph(self.get_object()))

    @extend_schema(summary='Export GEDCOM 7.0')
    @action(detail=True, methods=['get'])
    def gedcom(self, request, pk=None):
        tree = self.get_object()
        content = GedcomExporter(tree).run()
        response = HttpResponse(content, content_type='text/vnd.familysearch.gedcom; charset=utf-8')
        filename = f'{tree.name.replace(" ", "_")}.ged'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    @extend_schema(
        summary='Import GEDCOM',
        description='Envoyer le fichier en multipart (champ « file ») ou son contenu '
                    'texte dans le corps JSON (champ « content »).',
    )
    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, JSONParser])
    def import_gedcom(self, request, pk=None):
        tree = self.get_object()
        self.check_tree(tree)

        upload = request.FILES.get('file')
        if upload:
            filename = upload.name
            content = upload.read().decode('utf-8', errors='replace')
        else:
            filename = request.data.get('filename', 'import.ged')
            content = request.data.get('content', '')

        if not content.strip():
            raise ValidationError('Aucun contenu GEDCOM reçu.')

        job = ImportJob.objects.create(tree=tree, filename=filename, source='gedcom')
        try:
            with transaction.atomic():
                counts = GedcomImporter(tree).run(content)
        except Exception as exc:
            job.status = ImportJob.Status.FAILED
            job.log = str(exc)
            job.save(update_fields=['status', 'log'])
            raise ValidationError(f'Import impossible : {exc}') from exc

        job.status = ImportJob.Status.DONE
        job.counts = counts
        job.save(update_fields=['status', 'counts'])
        return Response(ImportJobSerializer(job).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary='Enregistrer les positions déplacées à la souris',
        request=LayoutBulkSerializer,
    )
    @action(detail=True, methods=['post'])
    def layout(self, request, pk=None):
        tree = self.get_object()
        self.check_tree(tree)

        serializer = LayoutBulkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        individual_ids = {i.pk for i in tree.individuals.all()}
        family_ids = {f.pk for f in tree.families.all()}
        saved = 0

        for position in serializer.validated_data['positions']:
            fields = {'x': position['x'], 'y': position['y'], 'pinned': position['pinned']}
            if indi_id := position.get('individual'):
                if indi_id not in individual_ids:
                    continue
                NodeLayout.objects.update_or_create(individual_id=indi_id, defaults=fields)
            else:
                fam_id = position['family']
                if fam_id not in family_ids:
                    continue
                FamilyLayout.objects.update_or_create(family_id=fam_id, defaults=fields)
            saved += 1

        return Response({'saved': saved})

    @extend_schema(
        summary='Géocoder tous les lieux de l’arbre',
        description='Clé facultative « geoapify_key » dans le corps ; sans elle, '
                    'Nominatim (OpenStreetMap) est utilisé.',
    )
    @action(detail=True, methods=['post'])
    def geocode(self, request, pk=None):
        tree = self.get_object()
        self.check_tree(tree)

        geocoder = Geocoder(_credentials(request))
        done, failed = 0, 0

        for place in tree.places.filter(latitude__isnull=True):
            try:
                hit = geocoder.geocode(place.name)
            except ProviderError as exc:
                return Response({'detail': exc.message, 'geocoded': done}, status=exc.status)
            if not hit:
                failed += 1
                continue
            place.latitude = hit['latitude']
            place.longitude = hit['longitude']
            place.geocode_provider = hit['provider']
            place.geocoded_at = timezone.now()
            place.save(update_fields=['latitude', 'longitude', 'geocode_provider', 'geocoded_at'])
            done += 1

        return Response({'geocoded': done, 'not_found': failed})

    @extend_schema(summary='Réglages de vue de l’arbre')
    @action(detail=True, methods=['get', 'patch'], url_path='settings')
    def view_settings(self, request, pk=None):
        tree = self.get_object()
        settings_obj, _ = TreeViewSettings.objects.get_or_create(tree=tree)

        if request.method == 'PATCH':
            self.check_tree(tree)
            serializer = TreeViewSettingsSerializer(settings_obj, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)

        return Response(TreeViewSettingsSerializer(settings_obj).data)


# ─────────────────────────────────────────────────────────────────────────────
# Données généalogiques
# ─────────────────────────────────────────────────────────────────────────────

class IndividualViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = Individual.objects.prefetch_related(
        'names', 'events__place', 'media_links__media',
    )
    serializer_class = IndividualSerializer

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return IndividualWriteSerializer
        return IndividualSerializer

    @extend_schema(
        summary='Frise chronologique de la vie d’un individu',
        description='Événements personnels, familiaux et naissances des enfants, '
                    'triés, avec le détail complet de chacun (cliquable dans la grande carte).',
    )
    @action(detail=True, methods=['get'])
    def timeline(self, request, pk=None):
        return Response(build_timeline(self.get_object()))

    @extend_schema(summary='Proches directs (parents, conjoints, enfants, fratrie)')
    @action(detail=True, methods=['get'])
    def relatives(self, request, pk=None):
        indi = self.get_object()
        parents, siblings, spouses, children = [], [], [], []

        for link in indi.families_as_child.select_related('family'):
            for spouse in link.family.spouses.select_related('individual'):
                parents.append(spouse.individual)
            for child in link.family.children.select_related('individual'):
                if child.individual_id != indi.pk:
                    siblings.append(child.individual)

        for link in indi.families_as_spouse.select_related('family'):
            for spouse in link.family.spouses.select_related('individual'):
                if spouse.individual_id != indi.pk:
                    spouses.append(spouse.individual)
            for child in link.family.children.select_related('individual'):
                children.append(child.individual)

        serialize = lambda people: IndividualSerializer(people, many=True).data  # noqa: E731
        return Response({
            'parents': serialize(parents),
            'siblings': serialize(siblings),
            'spouses': serialize(spouses),
            'children': serialize(children),
        })


class PersonalNameViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = PersonalName.objects.select_related('individual')
    serializer_class = PersonalNameSerializer
    tree_path = 'individual__tree'


class FamilyViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = Family.objects.prefetch_related('spouses', 'children', 'events')
    serializer_class = FamilySerializer

    @extend_schema(summary='Ajouter un conjoint à la famille')
    @action(detail=True, methods=['post'])
    def add_spouse(self, request, pk=None):
        family = self.get_object()
        individual = self._individual(request, family)
        role = request.data.get('role')
        if role not in SpouseRole.values:
            # Le rôle GEDCOM se déduit du sexe quand il n'est pas imposé.
            role = {
                Sex.MALE: SpouseRole.HUSBAND, Sex.FEMALE: SpouseRole.WIFE,
            }.get(individual.sex, SpouseRole.PARTNER)

        link, _ = FamilySpouse.objects.get_or_create(
            family=family, individual=individual, defaults={'role': role},
        )
        return Response(FamilySpouseSerializer(link).data, status=status.HTTP_201_CREATED)

    @extend_schema(summary='Ajouter un enfant à la famille')
    @action(detail=True, methods=['post'])
    def add_child(self, request, pk=None):
        family = self.get_object()
        individual = self._individual(request, family)
        link, _ = FamilyChild.objects.get_or_create(
            family=family,
            individual=individual,
            defaults={
                'pedigree': request.data.get('pedigree', 'BIRTH'),
                'order': family.children.count(),
            },
        )
        return Response(FamilyChildSerializer(link).data, status=status.HTTP_201_CREATED)

    def _individual(self, request, family) -> Individual:
        individual_id = request.data.get('individual')
        if not individual_id:
            raise ValidationError('Champ « individual » requis.')
        try:
            # Le même arbre : sans ce filtre, on pourrait rattacher l'individu d'autrui.
            return Individual.objects.get(pk=individual_id, tree=family.tree)
        except Individual.DoesNotExist as exc:
            raise ValidationError('Cet individu n’appartient pas à l’arbre.') from exc


class FamilySpouseViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = FamilySpouse.objects.all()
    serializer_class = FamilySpouseSerializer
    tree_path = 'family__tree'


class FamilyChildViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = FamilyChild.objects.all()
    serializer_class = FamilyChildSerializer
    tree_path = 'family__tree'


class EventViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = Event.objects.select_related('place')
    serializer_class = EventSerializer

    @extend_schema(
        summary='Catalogue des types d’événements',
        description='Alimente les listes déroulantes : libellé, catégorie de frise, '
                    'portée (individu ou famille) et nature (événement ou attribut).',
    )
    @action(detail=False, methods=['get'])
    def types(self, request):
        from .models import ATTRIBUTE_TAGS, FAMILY_TAGS

        return Response([
            {
                'tag': tag.value,
                'label': tag.label,
                'category': category_for(tag.value),
                'scope': 'FAMILY' if tag in FAMILY_TAGS else 'INDIVIDUAL',
                'is_attribute': tag in ATTRIBUTE_TAGS,
            }
            for tag in EventTag
        ])


class PlaceViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = Place.objects.all()
    serializer_class = PlaceSerializer

    @extend_schema(summary='Géocoder ce lieu')
    @action(detail=True, methods=['post'])
    def geocode(self, request, pk=None):
        place = self.get_object()
        self.check_tree(place.tree)

        try:
            hit = Geocoder(_credentials(request)).geocode(place.name)
        except ProviderError as exc:
            return Response({'detail': exc.message}, status=exc.status)

        if not hit:
            return Response({'detail': 'Lieu introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        place.latitude = hit['latitude']
        place.longitude = hit['longitude']
        place.geocode_provider = hit['provider']
        place.geocoded_at = timezone.now()
        place.save(update_fields=['latitude', 'longitude', 'geocode_provider', 'geocoded_at'])
        return Response(PlaceSerializer(place).data)


class MediaObjectViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = MediaObject.objects.all()
    serializer_class = MediaObjectSerializer
    parser_classes = [JSONParser, MultiPartParser]

    def create(self, request, *args, **kwargs):
        """Accepte aussi bien un upload multipart (« file ») que du base64 (« data_base64 »)."""
        upload = request.FILES.get('file')
        if upload is None:
            return super().create(request, *args, **kwargs)

        tree = self._tree_from(request.data.get('tree'))
        blob = upload.read()
        media = MediaObject.objects.create(
            tree=tree,
            title=request.data.get('title', '') or upload.name,
            filename=upload.name,
            mime=upload.content_type or 'application/octet-stream',
            data=blob,
            size=len(blob),
            checksum=hashlib.sha256(blob).hexdigest(),
        )

        # Rattachement immédiat si un individu est fourni : c'est le geste courant
        # (« ajouter la photo de cette personne »).
        if individual_id := request.data.get('individual'):
            individual = Individual.objects.filter(pk=individual_id, tree=tree).first()
            if individual:
                MediaLink.objects.create(
                    media=media,
                    individual=individual,
                    is_primary=not individual.media_links.filter(is_primary=True).exists(),
                )

        serializer = self.get_serializer(media)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def _tree_from(self, tree_id) -> Tree:
        tree = Tree.objects.filter(pk=tree_id, owner_email=self.request.user.email).first()
        if tree is None:
            raise ValidationError('Arbre inconnu ou non autorisé.')
        return tree

    @extend_schema(
        summary='Contenu binaire du média',
        description='Sert la photo. Le JWT peut être passé en « ?token= » : une balise '
                    '<img> ne peut pas envoyer d’en-tête Authorization.',
        parameters=[OpenApiParameter('token', str, description='JWT Keycloak (alternative à l’en-tête)')],
    )
    @action(
        detail=True,
        methods=['get'],
        url_path='file',
        authentication_classes=[KeycloakQueryTokenAuthentication],
    )
    def file(self, request, pk=None):
        media = self.get_object()
        if not media.data:
            return Response(
                {'detail': 'Ce média est externe.', 'external_url': media.external_url},
                status=status.HTTP_404_NOT_FOUND,
            )

        response = HttpResponse(bytes(media.data), content_type=media.mime)
        response['Content-Length'] = str(media.size or len(media.data))
        # Immuable : le contenu d'un média ne change jamais, seule sa fiche évolue.
        response['Cache-Control'] = 'private, max-age=86400'
        return response


class MediaLinkViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = MediaLink.objects.select_related('media')
    serializer_class = MediaLinkSerializer
    tree_path = 'media__tree'

    def perform_create(self, serializer):
        link = serializer.save()
        # Un seul portrait principal par individu.
        if link.is_primary and link.individual_id:
            MediaLink.objects.filter(
                individual_id=link.individual_id, is_primary=True,
            ).exclude(pk=link.pk).update(is_primary=False)


class SourceViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = Source.objects.all()
    serializer_class = SourceSerializer


class RepositoryViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = Repository.objects.all()
    serializer_class = RepositorySerializer


class CitationViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = Citation.objects.select_related('source')
    serializer_class = CitationSerializer
    tree_path = 'source__tree'


class SharedNoteViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = SharedNote.objects.all()
    serializer_class = SharedNoteSerializer


# ─────────────────────────────────────────────────────────────────────────────
# Styles, gabarits et positions
# ─────────────────────────────────────────────────────────────────────────────

class NodeStyleViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = NodeStyle.objects.all()
    serializer_class = NodeStyleSerializer


class StyleRuleViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = StyleRule.objects.select_related('style')
    serializer_class = StyleRuleSerializer


class EdgeStyleViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = EdgeStyle.objects.all()
    serializer_class = EdgeStyleSerializer


class CardTemplateViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = CardTemplate.objects.all()
    serializer_class = CardTemplateSerializer

    @extend_schema(
        summary='Aperçu du gabarit sur un individu réel',
        description='Renvoie la carte telle qu’elle serait affichée : champs résolus '
                    'et style appliqué. Sert à l’aperçu direct de la page de paramétrage.',
    )
    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):
        from .graph import individual_card, resolve_style

        template = self.get_object()
        individual_id = request.query_params.get('individual')

        individual = (
            Individual.objects
            .prefetch_related('names', 'events__place', 'media_links__media')
            .filter(tree=template.tree, **({'pk': individual_id} if individual_id else {}))
            .first()
        )
        if individual is None:
            return Response({'detail': 'Aucun individu dans cet arbre pour l’aperçu.'},
                            status=status.HTTP_404_NOT_FOUND)

        card = individual_card(individual)
        settings_obj, _ = TreeViewSettings.objects.get_or_create(tree=template.tree)
        rules = list(template.tree.style_rules.select_related('style').filter(enabled=True))
        card['style'] = resolve_style(card, rules, settings_obj.default_node_style, None)

        payload = {'card': card, 'template': CardTemplateSerializer(template).data}
        if template.kind == CardTemplate.Kind.FULL:
            payload['timeline'] = build_timeline(individual)
        return Response(payload)


class NodeLayoutViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = NodeLayout.objects.all()
    serializer_class = NodeLayoutSerializer
    tree_path = 'individual__tree'


class FamilyLayoutViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = FamilyLayout.objects.all()
    serializer_class = FamilyLayoutSerializer
    tree_path = 'family__tree'


class CustomFieldDefViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    queryset = CustomFieldDef.objects.all()
    serializer_class = CustomFieldDefSerializer


# ─────────────────────────────────────────────────────────────────────────────
# Enrichissement — fournisseurs externes
# ─────────────────────────────────────────────────────────────────────────────

def _credentials(request) -> dict:
    """
    Récupère les clés d'API envoyées par le client.

    Deux formes acceptées, jamais persistées côté serveur :
      · corps JSON : {"credentials": {"access_token": "…"}}
      · en-tête    : X-Provider-Key: <clé>   (raccourci mono-clé)
    """
    credentials = dict(request.data.get('credentials') or {}) if hasattr(request, 'data') else {}
    if header_key := request.headers.get('X-Provider-Key'):
        credentials.setdefault('access_token', header_key)
        credentials.setdefault('api_key', header_key)
    return credentials


class ProviderListView(APIView):
    """
    GET /api/enrich/providers/ — sources disponibles.

    Indique pour chacune si une clé est nécessaire, laquelle, et comment l'obtenir :
    c'est ce qui permet à l'interface de construire son formulaire de clés toute seule.
    """

    @extend_schema(summary='Fournisseurs disponibles et clés attendues')
    def get(self, request):
        return Response(describe_all())


class ProviderSearchView(APIView):
    """
    POST /api/enrich/search/ — cherche une personne chez un fournisseur.

    Corps :
      {"provider": "wikitree", "tree": 1,
       "query": {"given_name": "Jean", "surname": "Dupont", "birth_year": 1875},
       "credentials": {"access_token": "…"},   ← facultatif selon le fournisseur
       "save": true}                            ← conserve les candidats pour tri ultérieur
    """

    @extend_schema(summary='Rechercher chez un fournisseur externe')
    def post(self, request):
        provider_key = request.data.get('provider')
        if not provider_key:
            raise ValidationError('Champ « provider » requis.')

        raw = request.data.get('query') or {}
        query = SearchQuery(
            given_name=raw.get('given_name', ''),
            surname=raw.get('surname', ''),
            birth_year=_int(raw.get('birth_year')),
            death_year=_int(raw.get('death_year')),
            place=raw.get('place', ''),
            text=raw.get('text', ''),
            limit=min(_int(raw.get('limit')) or 10, 50),
        )
        if not query.full_name:
            raise ValidationError('Précisez au moins un nom ou un texte à rechercher.')

        try:
            provider = get_provider(provider_key, _credentials(request))
            results = provider.search(query)
        except ProviderError as exc:
            return Response({'detail': exc.message, 'provider': provider_key}, status=exc.status)

        tree = self._tree(request)
        if tree and request.data.get('save'):
            self._persist(tree, provider_key, results, request.data.get('individual'))

        return Response({
            'provider': provider_key,
            'count': len(results),
            'results': [r.as_dict() for r in results],
        })

    def _tree(self, request) -> Tree | None:
        tree_id = request.data.get('tree')
        if not tree_id:
            return None
        tree = Tree.objects.filter(pk=tree_id, owner_email=request.user.email).first()
        if tree is None:
            raise PermissionDenied('Arbre inconnu ou non autorisé.')
        return tree

    def _persist(self, tree, provider_key, results, individual_id) -> None:
        for result in results:
            EnrichmentMatch.objects.update_or_create(
                tree=tree,
                provider=provider_key,
                external_id=result.external_id,
                defaults={
                    'individual_id': individual_id,
                    'external_url': result.url,
                    'payload': result.as_dict(),
                    'score': result.score,
                },
            )


class ProviderFetchView(APIView):
    """
    POST /api/enrich/fetch/ — fiche complète d'une personne, proches inclus.

    Corps : {"provider": "wikitree", "external_id": "Clemens-1", "credentials": {…}}
    """

    @extend_schema(summary='Consulter une fiche chez un fournisseur')
    def post(self, request):
        provider_key = request.data.get('provider')
        external_id = request.data.get('external_id')
        if not provider_key or not external_id:
            raise ValidationError('Champs « provider » et « external_id » requis.')

        try:
            provider = get_provider(provider_key, _credentials(request))
            result = provider.fetch(external_id)
        except ProviderError as exc:
            return Response({'detail': exc.message, 'provider': provider_key}, status=exc.status)

        return Response(result.as_dict())


class ProviderImportView(APIView):
    """
    POST /api/enrich/import/ — verse une fiche externe dans l'arbre.

    Corps :
      {"provider": "wikitree", "external_id": "Clemens-1", "tree": 1,
       "credentials": {…},
       "target": 12,                ← individu à compléter (sinon un nouveau est créé)
       "with_relatives": true,      ← crée aussi parents/conjoints/enfants annoncés
       "with_photo": true}          ← télécharge le portrait dans la base

    Ne remplace jamais une valeur déjà saisie : l'import complète les trous.
    """

    @extend_schema(summary='Importer une fiche externe dans l’arbre')
    def post(self, request):
        provider_key = request.data.get('provider')
        external_id = request.data.get('external_id')
        tree_id = request.data.get('tree')
        if not (provider_key and external_id and tree_id):
            raise ValidationError('Champs « provider », « external_id » et « tree » requis.')

        tree = Tree.objects.filter(pk=tree_id, owner_email=request.user.email).first()
        if tree is None:
            raise PermissionDenied('Arbre inconnu ou non autorisé.')

        try:
            provider = get_provider(provider_key, _credentials(request))
            result = provider.fetch(external_id)
        except ProviderError as exc:
            return Response({'detail': exc.message, 'provider': provider_key}, status=exc.status)

        with transaction.atomic():
            individual = self._resolve_target(request, tree, result)
            created_relatives = []
            if request.data.get('with_relatives'):
                created_relatives = self._import_relatives(tree, individual, result)
            if request.data.get('with_photo', True) and result.photo_url:
                self._import_photo(tree, individual, result)

            EnrichmentMatch.objects.update_or_create(
                tree=tree, provider=provider_key, external_id=external_id,
                defaults={
                    'individual': individual,
                    'external_url': result.url,
                    'payload': result.as_dict(),
                    'score': result.score,
                    'status': EnrichmentMatch.Status.IMPORTED,
                },
            )

        return Response({
            'individual': IndividualSerializer(individual).data,
            'relatives_created': created_relatives,
        }, status=status.HTTP_201_CREATED)

    def _resolve_target(self, request, tree, result):
        target_id = request.data.get('target')
        if target_id:
            individual = Individual.objects.filter(pk=target_id, tree=tree).first()
            if individual is None:
                raise ValidationError('Individu cible introuvable dans cet arbre.')
            _merge(individual, result)
            return individual
        return _create_individual(tree, result)

    def _import_relatives(self, tree, individual, result) -> list[dict]:
        """
        Crée les proches annoncés et les rattache par une famille.

        Les personnes déjà présentes (même nom, même année de naissance) sont
        réutilisées : sans cela, chaque import dupliquerait la moitié de l'arbre.
        """
        created = []
        parents, spouses, children = [], [], []

        for relative in result.relatives:
            person = _find_or_create_relative(tree, relative)
            if person is None:
                continue
            created.append({'id': person.pk, 'relation': relative.relation, 'name': relative.name})
            if relative.relation in ('FATHER', 'MOTHER'):
                parents.append(person)
            elif relative.relation == 'SPOUSE':
                spouses.append(person)
            elif relative.relation == 'CHILD':
                children.append(person)

        if parents:
            family = _family_of_children(tree, [individual]) or Family.objects.create(tree=tree)
            for parent in parents:
                FamilySpouse.objects.get_or_create(
                    family=family, individual=parent,
                    defaults={'role': _role_for(parent)},
                )
            FamilyChild.objects.get_or_create(family=family, individual=individual)

        if spouses or children:
            family = _family_of_spouse(tree, individual) or Family.objects.create(tree=tree)
            FamilySpouse.objects.get_or_create(
                family=family, individual=individual, defaults={'role': _role_for(individual)},
            )
            for spouse in spouses:
                FamilySpouse.objects.get_or_create(
                    family=family, individual=spouse, defaults={'role': _role_for(spouse)},
                )
            for child in children:
                FamilyChild.objects.get_or_create(family=family, individual=child)

        return created

    def _import_photo(self, tree, individual, result) -> None:
        try:
            response = requests.get(
                result.photo_url, timeout=15,
                headers={'User-Agent': 'arbre-genealogique/1.0'},
            )
            response.raise_for_status()
        except requests.RequestException:
            # Une photo indisponible ne doit pas faire échouer l'import des données.
            return

        blob = response.content
        checksum = hashlib.sha256(blob).hexdigest()
        media, created = MediaObject.objects.get_or_create(
            tree=tree,
            checksum=checksum,
            defaults={
                'title': result.full_name,
                'data': blob,
                'mime': response.headers.get('Content-Type', 'image/jpeg').split(';')[0],
                'size': len(blob),
                'filename': result.photo_url.rsplit('/', 1)[-1][:250],
                'provider': result.provider,
                'attribution': result.url,
            },
        )
        if not individual.media_links.filter(media=media).exists():
            MediaLink.objects.create(
                media=media,
                individual=individual,
                is_primary=not individual.media_links.filter(is_primary=True).exists(),
            )


class EnrichmentMatchViewSet(OwnedTreeMixin, viewsets.ModelViewSet):
    """Candidats conservés : à examiner, acceptés ou rejetés."""

    queryset = EnrichmentMatch.objects.all()
    serializer_class = EnrichmentMatchSerializer


class ImportJobViewSet(OwnedTreeMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ImportJob.objects.all()
    serializer_class = ImportJobSerializer


# ─────────────────────────────────────────────────────────────────────────────
# Fusion d'une fiche externe dans nos modèles
# ─────────────────────────────────────────────────────────────────────────────

def _create_individual(tree: Tree, result) -> Individual:
    individual = Individual.objects.create(
        tree=tree,
        sex=result.sex if result.sex in Sex.values else Sex.UNKNOWN,
        note=result.description,
    )
    PersonalName.objects.create(
        individual=individual,
        givn=result.given_name,
        surn=result.surname,
        is_primary=True,
    )
    _add_event(tree, individual, EventTag.BIRT, result.birth_date, result.birth_place)
    _add_event(tree, individual, EventTag.DEAT, result.death_date, result.death_place)
    if result.occupation:
        Event.objects.create(
            tree=tree, individual=individual, tag=EventTag.OCCU,
            value=result.occupation, category=category_for(EventTag.OCCU),
        )
    if result.death_date:
        individual.is_living = False
        individual.save(update_fields=['is_living'])
    return individual


def _merge(individual: Individual, result) -> None:
    """Complète un individu existant sans écraser ce qui est déjà renseigné."""
    changed = []
    if individual.sex in ('', Sex.UNKNOWN) and result.sex in Sex.values:
        individual.sex = result.sex
        changed.append('sex')
    if not individual.note and result.description:
        individual.note = result.description
        changed.append('note')

    name = individual.names.filter(is_primary=True).first()
    if name is None and (result.given_name or result.surname):
        PersonalName.objects.create(
            individual=individual, givn=result.given_name, surn=result.surname, is_primary=True,
        )
    elif name:
        fields = []
        if not name.givn and result.given_name:
            name.givn = result.given_name
            fields.append('givn')
        if not name.surn and result.surname:
            name.surn = result.surname
            fields.append('surn')
        if fields:
            name.save(update_fields=fields)

    tree = individual.tree
    if not individual.events.filter(tag=EventTag.BIRT).exists():
        _add_event(tree, individual, EventTag.BIRT, result.birth_date, result.birth_place)
    if not individual.events.filter(tag=EventTag.DEAT).exists():
        _add_event(tree, individual, EventTag.DEAT, result.death_date, result.death_place)
        if result.death_date:
            individual.is_living = False
            changed.append('is_living')

    if changed:
        individual.save(update_fields=changed)


def _add_event(tree, individual, tag, date_raw: str, place_name: str = '') -> None:
    if not date_raw and not place_name:
        return

    place = None
    if place_name:
        place, _ = Place.objects.get_or_create(
            tree=tree,
            name=place_name[:300],
            defaults={'hierarchy': [p.strip() for p in place_name.split(',') if p.strip()]},
        )

    parsed = parse_date(_normalize_date(date_raw))
    Event.objects.create(
        tree=tree,
        individual=individual,
        tag=tag,
        place=place,
        category=category_for(tag),
        **parsed.as_fields(),
    )


def _normalize_date(value: str) -> str:
    """
    Les fournisseurs livrent « 1875-03-12 », « 1875-03 » ou déjà du GEDCOM.
    On ramène l'ISO vers la forme GEDCOM, seule comprise par le parseur.
    """
    if not value:
        return ''
    parts = value.split('-')
    months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
    try:
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            year, month, day = (int(p) for p in parts)
            return f'{day} {months[month - 1]} {year}'
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            year, month = (int(p) for p in parts)
            return f'{months[month - 1]} {year}'
    except (ValueError, IndexError):
        return value
    return value


def _find_or_create_relative(tree: Tree, relative) -> Individual | None:
    from .providers.base import extract_year, split_name

    given, surname = split_name(relative.name)
    if not (given or surname):
        return None

    birth_year = extract_year(relative.birth_date)
    candidates = Individual.objects.filter(
        tree=tree, names__givn__iexact=given, names__surn__iexact=surname,
    ).distinct()

    for candidate in candidates:
        if birth_year is None:
            return candidate
        birth = candidate.events.filter(tag=EventTag.BIRT).first()
        if birth and birth.date_start and birth.date_start.year == birth_year:
            return candidate
        if birth is None:
            return candidate

    individual = Individual.objects.create(
        tree=tree,
        sex=relative.sex if relative.sex in Sex.values else Sex.UNKNOWN,
        is_living=not relative.death_date,
    )
    PersonalName.objects.create(individual=individual, givn=given, surn=surname, is_primary=True)
    _add_event(tree, individual, EventTag.BIRT, _normalize_date(relative.birth_date))
    _add_event(tree, individual, EventTag.DEAT, _normalize_date(relative.death_date))
    return individual


def _family_of_children(tree: Tree, children: list[Individual]) -> Family | None:
    """Famille dans laquelle ces individus sont déjà enfants (pour y greffer les parents)."""
    for child in children:
        link = child.families_as_child.select_related('family').first()
        if link:
            return link.family
    return None


def _family_of_spouse(tree: Tree, individual: Individual) -> Family | None:
    link = individual.families_as_spouse.select_related('family').first()
    return link.family if link else None


def _role_for(individual: Individual) -> str:
    return {
        Sex.MALE: SpouseRole.HUSBAND,
        Sex.FEMALE: SpouseRole.WIFE,
    }.get(individual.sex, SpouseRole.PARTNER)


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
