from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CardTemplateViewSet,
    CitationViewSet,
    CustomFieldDefViewSet,
    EdgeStyleViewSet,
    EnrichmentMatchViewSet,
    EventViewSet,
    FamilyChildViewSet,
    FamilyLayoutViewSet,
    FamilySpouseViewSet,
    FamilyViewSet,
    ImportJobViewSet,
    IndividualViewSet,
    MediaLinkViewSet,
    MediaObjectViewSet,
    MeView,
    NodeLayoutViewSet,
    NodeStyleViewSet,
    PersonalNameViewSet,
    PlaceViewSet,
    ProviderFetchView,
    ProviderImportView,
    ProviderListView,
    ProviderSearchView,
    RepositoryViewSet,
    SharedNoteViewSet,
    SourceViewSet,
    StyleRuleViewSet,
    TreeShareViewSet,
    TreeViewSet,
)

router = DefaultRouter()

# ── Données généalogiques ────────────────────────────────────────────────────
router.register('trees', TreeViewSet)
router.register('tree-shares', TreeShareViewSet)
router.register('individuals', IndividualViewSet)
router.register('names', PersonalNameViewSet)
router.register('families', FamilyViewSet)
router.register('family-spouses', FamilySpouseViewSet)
router.register('family-children', FamilyChildViewSet)
router.register('events', EventViewSet)
router.register('places', PlaceViewSet)
router.register('media', MediaObjectViewSet)
router.register('media-links', MediaLinkViewSet)
router.register('sources', SourceViewSet)
router.register('repositories', RepositoryViewSet)
router.register('citations', CitationViewSet)
router.register('shared-notes', SharedNoteViewSet)

# ── Présentation (page « Paramétrage ») ──────────────────────────────────────
router.register('node-styles', NodeStyleViewSet)
router.register('style-rules', StyleRuleViewSet)
router.register('edge-styles', EdgeStyleViewSet)
router.register('card-templates', CardTemplateViewSet)
router.register('node-layouts', NodeLayoutViewSet)
router.register('family-layouts', FamilyLayoutViewSet)
router.register('custom-fields', CustomFieldDefViewSet)

# ── Enrichissement (page « Recherche ») ──────────────────────────────────────
router.register('enrichment-matches', EnrichmentMatchViewSet)
router.register('import-jobs', ImportJobViewSet)

urlpatterns = [
    path('me/', MeView.as_view()),

    # Les clés d'API voyagent dans le corps de ces trois requêtes (« credentials »)
    # ou dans l'en-tête X-Provider-Key. Rien n'est stocké côté serveur.
    path('enrich/providers/', ProviderListView.as_view()),
    path('enrich/search/', ProviderSearchView.as_view()),
    path('enrich/fetch/', ProviderFetchView.as_view()),
    path('enrich/import/', ProviderImportView.as_view()),

    path('', include(router.urls)),
]
