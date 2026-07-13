"""
Construction du graphe affiché par la page principale.

Le serveur fait le travail que le navigateur ne doit pas refaire à chaque rendu :
il calcule les générations, applique les règles de style conditionnelles et
aplatit chaque individu en une carte prête à dessiner. Le front n'a plus qu'à
placer les nœuds (dagre) et à tracer les liens.

La famille est matérialisée par un **nœud de jonction** : les conjoints s'y
relient, et les enfants en partent. C'est la façon la plus lisible de dessiner
deux parents pour un enfant sans croiser les liens.
"""
from collections import deque

from .models import (
    CardTemplate,
    EdgeStyle,
    EventTag,
    Family,
    Individual,
    NodeStyle,
    Pedigree,
    Tree,
    TreeViewSettings,
)

STYLE_FIELDS = [
    'background_color', 'background_gradient', 'border_color', 'border_width',
    'border_radius', 'border_style', 'text_color', 'accent_color', 'font_family',
    'font_size', 'font_weight', 'shadow', 'photo_shape', 'photo_size',
    'width', 'height', 'opacity',
]


def _style_dict(style: NodeStyle | None) -> dict:
    if style is None:
        return {}
    return {f: getattr(style, f) for f in STYLE_FIELDS}


# ─────────────────────────────────────────────────────────────────────────────
# Règles de style conditionnelles
# ─────────────────────────────────────────────────────────────────────────────

def _compare(actual, op: str, expected) -> bool:
    try:
        if op == 'eq':
            return actual == expected
        if op == 'ne':
            return actual != expected
        if op == 'lt':
            return actual is not None and actual < expected
        if op == 'lte':
            return actual is not None and actual <= expected
        if op == 'gt':
            return actual is not None and actual > expected
        if op == 'gte':
            return actual is not None and actual >= expected
        if op == 'in':
            return actual in (expected or [])
        if op == 'contains':
            return bool(actual) and str(expected).lower() in str(actual).lower()
        if op == 'exists':
            return bool(actual) is bool(expected)
    except TypeError:
        # Comparaison entre types incompatibles (« 1875 » < 1900) : la règle ne
        # s'applique pas, plutôt que de faire échouer tout le rendu.
        return False
    return False


def _field_value(ctx: dict, field: str):
    if field.startswith('custom.'):
        return (ctx.get('custom') or {}).get(field.split('.', 1)[1])
    return ctx.get(field)


def evaluate_condition(condition: dict, ctx: dict) -> bool:
    """Évalue {"all": [...]} / {"any": [...]} contre le contexte d'un individu."""
    if not condition:
        return False

    clauses_all = condition.get('all') or []
    clauses_any = condition.get('any') or []

    ok_all = all(
        _compare(_field_value(ctx, c.get('field', '')), c.get('op', 'eq'), c.get('value'))
        for c in clauses_all
    )
    ok_any = (
        any(
            _compare(_field_value(ctx, c.get('field', '')), c.get('op', 'eq'), c.get('value'))
            for c in clauses_any
        )
        if clauses_any else True
    )
    return ok_all and ok_any


def resolve_style(ctx: dict, rules: list, base: NodeStyle | None, override: NodeStyle | None) -> dict:
    """
    Style effectif d'une carte : style de base, puis chaque règle qui correspond
    (par priorité croissante, la plus prioritaire écrase), puis l'éventuel style
    forcé par l'utilisateur sur cet individu précis.
    """
    style = _style_dict(base)
    for rule in sorted(rules, key=lambda r: r.priority):
        if rule.enabled and evaluate_condition(rule.condition, ctx):
            style.update(_style_dict(rule.style))
    if override:
        style.update(_style_dict(override))
    return style


# ─────────────────────────────────────────────────────────────────────────────
# Générations
# ─────────────────────────────────────────────────────────────────────────────

def compute_generations(individuals, families) -> dict[int, int]:
    """
    Numérote les générations à partir d'une racine : parents = −1, enfants = +1.

    Le parcours est en largeur et traverse aussi les conjoints (même génération),
    ce qui permet de rattacher les branches par alliance. Les individus d'une
    composante non connectée à la racine repartent d'une racine locale à 0.
    """
    parents_of: dict[int, list[int]] = {i.pk: [] for i in individuals}
    children_of: dict[int, list[int]] = {i.pk: [] for i in individuals}
    spouses_of: dict[int, list[int]] = {i.pk: [] for i in individuals}

    for fam in families:
        spouse_ids = [s.individual_id for s in fam.spouses.all()]
        child_ids = [c.individual_id for c in fam.children.all()]
        for a in spouse_ids:
            for b in spouse_ids:
                if a != b and b not in spouses_of.get(a, []):
                    spouses_of.setdefault(a, []).append(b)
        for child in child_ids:
            for parent in spouse_ids:
                parents_of.setdefault(child, []).append(parent)
                children_of.setdefault(parent, []).append(child)

    generations: dict[int, int] = {}
    remaining = {i.pk for i in individuals}

    while remaining:
        # Racine de la composante : on préfère quelqu'un sans parent connu (le plus
        # ancien), sinon n'importe qui — sans quoi un cycle bloquerait la boucle.
        root = next((pk for pk in remaining if not parents_of.get(pk)), next(iter(remaining)))
        generations[root] = 0
        queue = deque([root])

        while queue:
            current = queue.popleft()
            remaining.discard(current)
            gen = generations[current]

            for parent in parents_of.get(current, []):
                if parent not in generations:
                    generations[parent] = gen - 1
                    queue.append(parent)
            for child in children_of.get(current, []):
                if child not in generations:
                    generations[child] = gen + 1
                    queue.append(child)
            for spouse in spouses_of.get(current, []):
                if spouse not in generations:
                    generations[spouse] = gen
                    queue.append(spouse)

    return generations


# ─────────────────────────────────────────────────────────────────────────────
# Graphe
# ─────────────────────────────────────────────────────────────────────────────

def _primary_name(indi: Individual):
    return next((n for n in indi.names.all() if n.is_primary), None) or indi.names.first()


def _event(indi: Individual, *tags):
    return next((e for e in indi.events.all() if e.tag in tags), None)


def _year(event) -> int | None:
    return event.date_start.year if event and event.date_start else None


def individual_card(indi: Individual) -> dict:
    """Aplatit un individu en les champs qu'une carte d'identité sait afficher."""
    name = _primary_name(indi)
    birth = _event(indi, EventTag.BIRT, EventTag.CHR, EventTag.BAPM)
    death = _event(indi, EventTag.DEAT, EventTag.BURI, EventTag.CREM)
    occupation = _event(indi, EventTag.OCCU)
    residence = _event(indi, EventTag.RESI)

    photo_link = next((l for l in indi.media_links.all() if l.is_primary), None)
    photo_url = None
    crop = None
    if photo_link:
        media = photo_link.media
        photo_url = f'/api/media/{media.pk}/file/' if media.data else (media.external_url or None)
        if photo_link.crop_width:
            crop = {
                'x': photo_link.crop_x, 'y': photo_link.crop_y,
                'width': photo_link.crop_width, 'height': photo_link.crop_height,
            }

    birth_year, death_year = _year(birth), _year(death)
    lifespan = ''
    if birth_year or death_year:
        lifespan = f'{birth_year or "?"} – {death_year or ("" if indi.is_living else "?")}'

    return {
        'id': indi.pk,
        'xref_id': indi.xref_id,
        'sex': indi.sex,
        'is_living': indi.is_living,
        'confidential': indi.confidential,
        'given_name': name.givn if name else '',
        'surname': ' '.join(p for p in (name.spfx, name.surn) if p) if name else '',
        'nickname': name.nick if name else '',
        'full_name': str(name) if name else '',
        'birth_date': birth.date_raw if birth else '',
        'birth_place': birth.place.name if birth and birth.place else '',
        'birth_year': birth_year,
        'death_date': death.date_raw if death else '',
        'death_place': death.place.name if death and death.place else '',
        'death_year': death_year,
        'lifespan': lifespan,
        'occupation': occupation.value if occupation else '',
        'residence': residence.value if residence else '',
        'note': indi.note,
        'custom': indi.custom,
        'photo_url': photo_url,
        'photo_crop': crop,
        'has_photo': photo_url is not None,
    }


def build_graph(tree: Tree) -> dict:
    """Assemble nœuds, jonctions familiales, liens et styles pour la page principale."""
    settings, _ = TreeViewSettings.objects.get_or_create(tree=tree)

    individuals = list(
        tree.individuals
        .prefetch_related('names', 'events__place', 'media_links__media', 'layout')
        .select_related('layout__style_override')
    )
    families = list(tree.families.prefetch_related('spouses', 'children', 'events').select_related('layout'))
    rules = list(tree.style_rules.select_related('style').filter(enabled=True))
    base_style = settings.default_node_style

    generations = compute_generations(individuals, families)

    nodes = []
    for indi in individuals:
        card = individual_card(indi)
        card['generation'] = generations.get(indi.pk, 0)

        layout = getattr(indi, 'layout', None)
        card['x'] = layout.x if layout else 0
        card['y'] = layout.y if layout else 0
        card['pinned'] = layout.pinned if layout else False
        card['collapsed'] = layout.collapsed if layout else False
        card['hidden'] = layout.hidden if layout else False
        card['style'] = resolve_style(
            card, rules, base_style, layout.style_override if layout else None,
        )
        nodes.append(card)

    family_nodes, edges = [], []
    for fam in families:
        layout = getattr(fam, 'layout', None)
        marriage = next((e for e in fam.events.all() if e.tag == EventTag.MARR), None)
        spouse_ids = [s.individual_id for s in fam.spouses.all()]

        family_nodes.append({
            'id': fam.pk,
            'union_type': fam.union_type,
            'marriage_date': marriage.date_raw if marriage else '',
            'spouses': spouse_ids,
            'children': [c.individual_id for c in fam.children.all()],
            'x': layout.x if layout else 0,
            'y': layout.y if layout else 0,
            'pinned': layout.pinned if layout else False,
            # La jonction se place entre les générations des parents et des enfants.
            'generation': (
                max((generations.get(s, 0) for s in spouse_ids), default=0)
                if spouse_ids else 0
            ),
        })

        for spouse in fam.spouses.all():
            edges.append({
                'source': f'i{spouse.individual_id}',
                'target': f'f{fam.pk}',
                'kind': 'SPOUSE',
                'role': spouse.role,
            })
        for child in fam.children.all():
            edges.append({
                'source': f'f{fam.pk}',
                'target': f'i{child.individual_id}',
                'kind': 'ADOPTED' if child.pedigree != Pedigree.BIRTH else 'PARENT_CHILD',
                'pedigree': child.pedigree,
            })

    edge_styles = {
        style.applies_to: {
            'color': style.color, 'width': style.width, 'dash': style.dash,
            'curve': style.curve, 'marker_end': style.marker_end, 'opacity': style.opacity,
        }
        for style in tree.edge_styles.all()
    }

    mini = settings.default_mini_template or tree.card_templates.filter(
        kind=CardTemplate.Kind.MINI, is_default=True,
    ).first()
    full = settings.default_full_template or tree.card_templates.filter(
        kind=CardTemplate.Kind.FULL, is_default=True,
    ).first()

    return {
        'tree': {'id': tree.pk, 'name': tree.name},
        'settings': {
            'layout_algorithm': settings.layout_algorithm,
            'orientation': settings.orientation,
            'node_spacing_x': settings.node_spacing_x,
            'node_spacing_y': settings.node_spacing_y,
            'generation_spacing': settings.generation_spacing,
            'zoom': settings.zoom,
            'pan_x': settings.pan_x,
            'pan_y': settings.pan_y,
            'background_color': settings.background_color,
            'show_grid': settings.show_grid,
            'snap_to_grid': settings.snap_to_grid,
            'grid_size': settings.grid_size,
            'show_spouses': settings.show_spouses,
            'show_photos': settings.show_photos,
            'show_dates': settings.show_dates,
            'root_individual': settings.root_individual_id,
        },
        'nodes': nodes,
        'families': family_nodes,
        'edges': edges,
        'edge_styles': edge_styles,
        'mini_template': _template_dict(mini),
        'full_template': _template_dict(full),
    }


def _template_dict(template: CardTemplate | None) -> dict | None:
    if template is None:
        return None
    return {
        'id': template.pk,
        'kind': template.kind,
        'name': template.name,
        'photo_position': template.photo_position,
        'photo_size': template.photo_size,
        'photo_shape': template.photo_shape,
        'photo_placeholder': template.photo_placeholder,
        'fields': template.fields,
        'date_format': template.date_format,
        'deceased_marker': template.deceased_marker,
        'show_timeline': template.show_timeline,
        'timeline_categories': template.timeline_categories,
        'timeline_orientation': template.timeline_orientation,
        'show_periods': template.show_periods,
        'show_sources': template.show_sources,
        'show_gallery': template.show_gallery,
        'background_color': template.background_color,
        'custom_css': template.custom_css,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Frise chronologique (grande carte)
# ─────────────────────────────────────────────────────────────────────────────

def build_timeline(indi: Individual) -> dict:
    """
    Frise de la vie d'un individu : ses événements personnels + ceux de ses
    familles (mariages, naissances des enfants), triés et bornés par sa vie.

    Chaque entrée est cliquable côté interface : le détail complet (lieu, source,
    cause, note) voyage avec elle, il n'y a pas de second aller-retour au serveur.
    """
    entries = []

    for event in indi.events.all():
        entries.append(_timeline_entry(event, scope='INDIVIDUAL'))

    for link in indi.families_as_spouse.select_related('family'):
        for event in link.family.events.all():
            entries.append(_timeline_entry(event, scope='FAMILY'))

    # Naissance des enfants : jalon majeur d'une vie, absent des événements propres.
    for link in indi.families_as_spouse.select_related('family'):
        for child_link in link.family.children.select_related('individual'):
            child = child_link.individual
            birth = _event(child, EventTag.BIRT)
            if birth:
                entry = _timeline_entry(birth, scope='CHILD')
                name = _primary_name(child)
                entry['label'] = f'Naissance de {name}' if name else 'Naissance d’un enfant'
                entry['related_individual'] = child.pk
                entries.append(entry)

    entries.sort(key=lambda e: (e['start'] is None, e['start'] or '', e['sort_order']))

    years = [e['start_year'] for e in entries if e['start_year']]
    return {
        'individual': indi.pk,
        'entries': entries,
        'span': {'from': min(years), 'to': max(years)} if years else None,
    }


def _timeline_entry(event, scope: str) -> dict:
    return {
        'id': event.pk,
        'scope': scope,
        'tag': event.tag,
        'label': event.custom_type or event.get_tag_display(),
        'value': event.value,
        'category': event.category,
        'is_period': event.is_period,
        'date_raw': event.date_raw,
        'date_phrase': event.date_phrase,
        'start': event.date_start.isoformat() if event.date_start else None,
        'end': event.date_end.isoformat() if event.date_end else None,
        'start_year': event.date_start.year if event.date_start else None,
        'end_year': event.date_end.year if event.date_end else None,
        'place': event.place.name if event.place else '',
        'latitude': float(event.place.latitude) if event.place and event.place.latitude else None,
        'longitude': float(event.place.longitude) if event.place and event.place.longitude else None,
        'age': event.age,
        'cause': event.cause,
        'agency': event.agency,
        'note': event.note,
        'color': event.color,
        'icon': event.icon,
        'sort_order': event.sort_order,
    }
