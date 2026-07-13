"""
Contenu par défaut créé avec chaque arbre : gabarits de cartes, styles, réglages.

Un arbre neuf doit être immédiatement affichable et modifiable ; sans ces objets,
la page principale n'aurait rien à dessiner et la page de paramétrage rien à éditer.
"""
from .models import (
    CardTemplate,
    EdgeStyle,
    EventCategory,
    NodeStyle,
    Sex,
    StyleRule,
    Tree,
    TreeViewSettings,
)

#: Mini-carte : photo à gauche, puis nom, prénom, naissance et décès empilés à droite.
MINI_FIELDS = [
    {'key': 'surname',     'label': 'Nom',              'show': True, 'order': 1,
     'bold': True,  'size': 14, 'color': '#1f2733', 'uppercase': True},
    {'key': 'given_name',  'label': 'Prénom',           'show': True, 'order': 2,
     'bold': False, 'size': 13, 'color': '#1f2733', 'uppercase': False},
    {'key': 'birth_date',  'label': 'Naissance',        'show': True, 'order': 3,
     'bold': False, 'size': 11, 'color': '#6b7684', 'prefix': '★ '},
    # hide_if_living : la date de décès n'apparaît que si la personne est décédée.
    {'key': 'death_date',  'label': 'Décès',            'show': True, 'order': 4,
     'bold': False, 'size': 11, 'color': '#6b7684', 'prefix': '† ', 'hide_if_living': True},
]

#: Grande carte : mêmes informations, plus le contexte (lieux, profession).
FULL_FIELDS = [
    {'key': 'surname',      'label': 'Nom',        'show': True, 'order': 1,
     'bold': True,  'size': 24, 'color': '#1f2733', 'uppercase': True},
    {'key': 'given_name',   'label': 'Prénom',     'show': True, 'order': 2,
     'bold': False, 'size': 20, 'color': '#1f2733'},
    {'key': 'nickname',     'label': 'Surnom',     'show': False, 'order': 3,
     'bold': False, 'size': 14, 'color': '#6b7684'},
    {'key': 'birth_date',   'label': 'Naissance',  'show': True, 'order': 4,
     'bold': False, 'size': 14, 'color': '#42505f', 'prefix': '★ '},
    {'key': 'birth_place',  'label': 'Lieu de naissance', 'show': True, 'order': 5,
     'bold': False, 'size': 13, 'color': '#6b7684'},
    {'key': 'death_date',   'label': 'Décès',      'show': True, 'order': 6,
     'bold': False, 'size': 14, 'color': '#42505f', 'prefix': '† ', 'hide_if_living': True},
    {'key': 'death_place',  'label': 'Lieu de décès', 'show': True, 'order': 7,
     'bold': False, 'size': 13, 'color': '#6b7684', 'hide_if_living': True},
    {'key': 'age',          'label': 'Âge',        'show': True, 'order': 8,
     'bold': False, 'size': 13, 'color': '#6b7684'},
    {'key': 'occupation',   'label': 'Profession', 'show': True, 'order': 9,
     'bold': False, 'size': 13, 'color': '#6b7684'},
]

#: Couleur de chaque bande de la frise chronologique.
TIMELINE_COLORS = {
    EventCategory.LIFE: '#1976d2',
    EventCategory.FAMILY: '#c2185b',
    EventCategory.EDUCATION: '#7b1fa2',
    EventCategory.WORK: '#00796b',
    EventCategory.MILITARY: '#5d4037',
    EventCategory.RESIDENCE: '#f57c00',
    EventCategory.RELIGION: '#455a64',
    EventCategory.HEALTH: '#d32f2f',
    EventCategory.LEGAL: '#616161',
    EventCategory.MIGRATION: '#0288d1',
    EventCategory.OTHER: '#9e9e9e',
}


def build_defaults(tree: Tree) -> TreeViewSettings:
    """Crée styles, règles, gabarits et réglages pour un arbre neuf."""

    base_style = NodeStyle.objects.create(
        tree=tree, name='Standard', is_default=True,
    )
    male_style = NodeStyle.objects.create(
        tree=tree, name='Homme',
        background_color='#f3f8ff', border_color='#8ab4f8', accent_color='#1a73e8',
    )
    female_style = NodeStyle.objects.create(
        tree=tree, name='Femme',
        background_color='#fff5f8', border_color='#f4a0bf', accent_color='#c2185b',
    )
    deceased_style = NodeStyle.objects.create(
        tree=tree, name='Décédé',
        background_color='#f7f8f9', border_color='#c9cfd6',
        text_color='#5a646f', opacity=0.92,
    )

    # Les règles sont évaluées par priorité décroissante ; le sexe teinte la carte,
    # le décès (priorité plus haute) la grise par-dessus.
    StyleRule.objects.create(
        tree=tree, name='Hommes', style=male_style, priority=10,
        condition={'all': [{'field': 'sex', 'op': 'eq', 'value': Sex.MALE}]},
    )
    StyleRule.objects.create(
        tree=tree, name='Femmes', style=female_style, priority=10,
        condition={'all': [{'field': 'sex', 'op': 'eq', 'value': Sex.FEMALE}]},
    )
    StyleRule.objects.create(
        tree=tree, name='Personnes décédées', style=deceased_style, priority=20,
        condition={'all': [{'field': 'is_living', 'op': 'eq', 'value': False}]},
    )

    parent_edge = EdgeStyle.objects.create(
        tree=tree, name='Filiation', applies_to=EdgeStyle.AppliesTo.PARENT_CHILD,
        is_default=True, color='#b6c0cd', width=2, curve='orthogonal',
    )
    EdgeStyle.objects.create(
        tree=tree, name='Union', applies_to=EdgeStyle.AppliesTo.SPOUSE,
        color='#e0a3bd', width=2, dash='solid', curve='straight',
    )
    EdgeStyle.objects.create(
        tree=tree, name='Filiation adoptive', applies_to=EdgeStyle.AppliesTo.ADOPTED,
        color='#b6c0cd', width=2, dash='dashed', curve='orthogonal',
    )

    mini = CardTemplate.objects.create(
        tree=tree, kind=CardTemplate.Kind.MINI, name='Mini-carte standard', is_default=True,
        photo_position='LEFT', photo_size=52, photo_shape='rounded',
        fields=MINI_FIELDS, show_timeline=False, show_sources=False, show_gallery=False,
    )
    full = CardTemplate.objects.create(
        tree=tree, kind=CardTemplate.Kind.FULL, name='Grande carte standard', is_default=True,
        photo_position='TOP_LEFT', photo_size=160, photo_shape='rounded',
        fields=FULL_FIELDS, show_timeline=True, show_periods=True,
        show_sources=True, show_gallery=True,
    )

    return TreeViewSettings.objects.create(
        tree=tree,
        default_mini_template=mini,
        default_full_template=full,
        default_node_style=base_style,
        default_edge_style=parent_edge,
    )
