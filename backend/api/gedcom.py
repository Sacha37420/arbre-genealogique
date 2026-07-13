"""
Lecture et écriture GEDCOM 7.0 (rétrocompatible 5.5.1 en import).

Trois responsabilités :
  · parse_date / format_date — la grammaire des dates GEDCOM, qui est la partie
    la plus piégeuse du format (ABT, BET…AND, FROM…TO, calendriers non grégoriens) ;
  · GedcomImporter — un fichier .ged → les modèles Django ;
  · GedcomExporter — un arbre → un fichier .ged réimportable ailleurs.

Référence : https://gedcom.io/specifications/FamilySearchGEDCOMv7.html
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field as dc_field

from .models import (
    ATTRIBUTE_TAGS,
    FAMILY_TAGS,
    Calendar,
    ChildStatus,
    DateModifier,
    DatePrecision,
    Event,
    EventCategory,
    EventTag,
    Family,
    FamilyChild,
    FamilySpouse,
    Individual,
    MediaLink,
    MediaObject,
    NameType,
    Pedigree,
    PersonalName,
    Place,
    Sex,
    Source,
    SpouseRole,
    Tree,
    UnionType,
)

MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
          'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

CALENDAR_TAGS = {
    'GREGORIAN': Calendar.GREGORIAN,
    'JULIAN': Calendar.JULIAN,
    'FRENCH_R': Calendar.FRENCH_R,
    'HEBREW': Calendar.HEBREW,
}

#: Catégorie de frise chronologique déduite du tag GEDCOM.
TAG_CATEGORY = {
    EventTag.BIRT: EventCategory.LIFE,
    EventTag.DEAT: EventCategory.LIFE,
    EventTag.BURI: EventCategory.LIFE,
    EventTag.CREM: EventCategory.LIFE,
    EventTag.ADOP: EventCategory.FAMILY,
    EventTag.MARR: EventCategory.FAMILY,
    EventTag.MARB: EventCategory.FAMILY,
    EventTag.MARC: EventCategory.FAMILY,
    EventTag.MARL: EventCategory.FAMILY,
    EventTag.MARS: EventCategory.FAMILY,
    EventTag.ENGA: EventCategory.FAMILY,
    EventTag.DIV: EventCategory.FAMILY,
    EventTag.DIVF: EventCategory.FAMILY,
    EventTag.ANUL: EventCategory.FAMILY,
    EventTag.SEPA: EventCategory.FAMILY,
    EventTag.EDUC: EventCategory.EDUCATION,
    EventTag.GRAD: EventCategory.EDUCATION,
    EventTag.OCCU: EventCategory.WORK,
    EventTag.RETI: EventCategory.WORK,
    EventTag.RESI: EventCategory.RESIDENCE,
    EventTag.PROP: EventCategory.RESIDENCE,
    EventTag.EMIG: EventCategory.MIGRATION,
    EventTag.IMMI: EventCategory.MIGRATION,
    EventTag.NATU: EventCategory.MIGRATION,
    EventTag.BAPM: EventCategory.RELIGION,
    EventTag.CHR: EventCategory.RELIGION,
    EventTag.CONF: EventCategory.RELIGION,
    EventTag.FCOM: EventCategory.RELIGION,
    EventTag.ORDN: EventCategory.RELIGION,
    EventTag.BLES: EventCategory.RELIGION,
    EventTag.BARM: EventCategory.RELIGION,
    EventTag.BASM: EventCategory.RELIGION,
    EventTag.RELI: EventCategory.RELIGION,
    EventTag.DSCR: EventCategory.HEALTH,
    EventTag.WILL: EventCategory.LEGAL,
    EventTag.PROB: EventCategory.LEGAL,
    EventTag.CENS: EventCategory.LEGAL,
    EventTag.SSN: EventCategory.LEGAL,
    EventTag.IDNO: EventCategory.LEGAL,
}


def category_for(tag: str) -> str:
    return TAG_CATEGORY.get(tag, EventCategory.OTHER)


# ─────────────────────────────────────────────────────────────────────────────
# Dates
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ParsedDate:
    """Résultat de l'analyse d'une valeur DATE, prêt à peupler un Event."""

    raw: str = ''
    modifier: str = DateModifier.UNKNOWN
    start: dt.date | None = None
    end: dt.date | None = None
    precision: str = DatePrecision.NONE
    calendar: str = Calendar.GREGORIAN
    phrase: str = ''

    @property
    def is_period(self) -> bool:
        return self.modifier in (DateModifier.PERIOD, DateModifier.BETWEEN)

    def as_fields(self) -> dict:
        return {
            'date_raw': self.raw,
            'date_modifier': self.modifier,
            'date_start': self.start,
            'date_end': self.end,
            'date_precision': self.precision,
            'calendar': self.calendar,
            'date_phrase': self.phrase,
            'is_period': self.is_period,
        }


_SIMPLE_DATE = re.compile(
    r'^(?:(?P<day>\d{1,2})\s+)?(?:(?P<month>[A-Z]{3})\s+)?(?P<year>\d{1,4})$'
)


def _parse_simple(token: str) -> tuple[dt.date | None, dt.date | None, str]:
    """
    « 12 JAN 1875 » / « JAN 1875 » / « 1875 » → (début, fin, précision).

    Une date imprécise devient un intervalle : « JAN 1875 » couvre le mois entier.
    Cela permet de trier et de placer l'événement sur la frise sans inventer un jour.
    """
    m = _SIMPLE_DATE.match(token.strip().upper())
    if not m:
        return None, None, DatePrecision.NONE

    year = int(m.group('year'))
    month_tag = m.group('month')
    day = m.group('day')

    try:
        if month_tag and month_tag in MONTHS:
            month = MONTHS.index(month_tag) + 1
            if day:
                d = dt.date(year, month, int(day))
                return d, d, DatePrecision.DAY
            start = dt.date(year, month, 1)
            end = (dt.date(year + (month == 12), (month % 12) + 1, 1) - dt.timedelta(days=1))
            return start, end, DatePrecision.MONTH
        return dt.date(year, 1, 1), dt.date(year, 12, 31), DatePrecision.YEAR
    except ValueError:
        # Date impossible (30 FEB) : on garde l'année seule plutôt que de tout perdre.
        try:
            return dt.date(year, 1, 1), dt.date(year, 12, 31), DatePrecision.YEAR
        except ValueError:
            return None, None, DatePrecision.NONE


def parse_date(raw: str) -> ParsedDate:
    """Analyse une valeur DATE GEDCOM sous toutes ses formes."""
    if not raw or not raw.strip():
        return ParsedDate()

    value = raw.strip()
    result = ParsedDate(raw=value)
    upper = value.upper()

    # Texte libre entre parenthèses : « (vers la fin du règne) »
    if upper.startswith('(') and upper.endswith(')'):
        result.modifier = DateModifier.PHRASE
        result.phrase = value[1:-1]
        return result

    # Calendrier explicite en préfixe
    for tag, cal in CALENDAR_TAGS.items():
        if upper.startswith(tag + ' '):
            result.calendar = cal
            upper = upper[len(tag) + 1:].strip()
            break

    # Les calendriers non grégoriens ne sont pas convertis : on conserve le texte
    # brut pour ne rien perdre, sans prétendre à une date exploitable.
    if result.calendar != Calendar.GREGORIAN:
        result.modifier = DateModifier.PHRASE
        result.phrase = upper
        return result

    def finish(mod: str, a: str, b: str = '') -> ParsedDate:
        s1, e1, p1 = _parse_simple(a)
        result.modifier = mod
        result.precision = p1
        if b:
            s2, e2, _ = _parse_simple(b)
            result.start, result.end = s1, (e2 or s2)
        else:
            result.start, result.end = s1, e1
        return result

    if m := re.match(r'^BET\s+(.+?)\s+AND\s+(.+)$', upper):
        return finish(DateModifier.BETWEEN, m.group(1), m.group(2))

    if m := re.match(r'^FROM\s+(.+?)\s+TO\s+(.+)$', upper):
        return finish(DateModifier.PERIOD, m.group(1), m.group(2))

    if m := re.match(r'^FROM\s+(.+)$', upper):
        return finish(DateModifier.FROM, m.group(1))

    if m := re.match(r'^TO\s+(.+)$', upper):
        return finish(DateModifier.TO, m.group(1))

    for kw, mod in (('ABT', DateModifier.ABOUT), ('CAL', DateModifier.CALCULATED),
                    ('EST', DateModifier.ESTIMATED), ('BEF', DateModifier.BEFORE),
                    ('AFT', DateModifier.AFTER)):
        if m := re.match(rf'^{kw}\s+(.+)$', upper):
            return finish(mod, m.group(1))

    parsed = finish(DateModifier.EXACT, upper)
    if parsed.start is None:
        # Non reconnu : conservé tel quel plutôt que jeté.
        parsed.modifier = DateModifier.PHRASE
        parsed.phrase = value
    return parsed


def format_date(event: Event) -> str:
    """Reconstruit la valeur DATE GEDCOM d'un événement (pour l'export)."""
    if event.date_raw:
        return event.date_raw
    if event.date_phrase:
        return f'({event.date_phrase})'
    if not event.date_start:
        return ''

    def fmt(d: dt.date, precision: str) -> str:
        if precision == DatePrecision.YEAR:
            return str(d.year)
        if precision == DatePrecision.MONTH:
            return f'{MONTHS[d.month - 1]} {d.year}'
        return f'{d.day} {MONTHS[d.month - 1]} {d.year}'

    start = fmt(event.date_start, event.date_precision)
    if event.date_modifier == DateModifier.BETWEEN and event.date_end:
        return f'BET {start} AND {fmt(event.date_end, event.date_precision)}'
    if event.date_modifier == DateModifier.PERIOD and event.date_end:
        return f'FROM {start} TO {fmt(event.date_end, event.date_precision)}'
    prefix = {
        DateModifier.ABOUT: 'ABT ', DateModifier.CALCULATED: 'CAL ',
        DateModifier.ESTIMATED: 'EST ', DateModifier.BEFORE: 'BEF ',
        DateModifier.AFTER: 'AFT ', DateModifier.FROM: 'FROM ',
        DateModifier.TO: 'TO ',
    }.get(event.date_modifier, '')
    return f'{prefix}{start}'


# ─────────────────────────────────────────────────────────────────────────────
# Analyse lexicale : le fichier → un arbre de lignes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Node:
    level: int
    tag: str
    value: str = ''
    xref: str = ''
    children: list['Node'] = dc_field(default_factory=list)

    def first(self, tag: str) -> 'Node | None':
        return next((c for c in self.children if c.tag == tag), None)

    def value_of(self, tag: str, default: str = '') -> str:
        node = self.first(tag)
        return node.value if node else default

    def all(self, *tags: str) -> list['Node']:
        return [c for c in self.children if c.tag in tags]


_LINE = re.compile(r'^\s*(?P<level>\d+)\s+(?:(?P<xref>@[^@]+@)\s+)?(?P<tag>\w+)(?:\s(?P<value>.*))?$')


def tokenize(text: str) -> list[Node]:
    """Transforme le texte GEDCOM en une forêt de Node (un par enregistrement de niveau 0)."""
    roots: list[Node] = []
    stack: list[Node] = []

    for line in text.splitlines():
        if not line.strip():
            continue
        m = _LINE.match(line)
        if not m:
            continue

        level = int(m.group('level'))
        node = Node(
            level=level,
            tag=m.group('tag'),
            value=(m.group('value') or '').strip(),
            xref=(m.group('xref') or '').strip(),
        )

        # CONT/CONC prolongent la valeur de la ligne précédente (texte multiligne).
        if node.tag in ('CONT', 'CONC') and stack:
            parent = stack[-1]
            sep = '\n' if node.tag == 'CONT' else ''
            parent.value = f'{parent.value}{sep}{node.value}'
            continue

        del stack[level:]
        if level == 0:
            roots.append(node)
        elif stack:
            stack[level - 1].children.append(node)
        stack.append(node)

    return roots


# ─────────────────────────────────────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────────────────────────────────────

class GedcomImporter:
    """
    Charge un fichier GEDCOM dans un Tree existant.

    Les enregistrements sont créés en deux passes : d'abord les individus et les
    médias (pour connaître tous les xref), ensuite les familles, qui pointent
    vers eux. Sans cela, un CHIL référençant un individu défini plus bas dans le
    fichier serait perdu.
    """

    def __init__(self, tree: Tree) -> None:
        self.tree = tree
        self.individuals: dict[str, Individual] = {}
        self.media: dict[str, MediaObject] = {}
        self.sources: dict[str, Source] = {}
        self.places: dict[str, Place] = {}
        self.counts = {'individuals': 0, 'families': 0, 'events': 0, 'media': 0, 'sources': 0}

    # ── Utilitaires ───────────────────────────────────────────────────────
    def _place(self, node: Node) -> Place | None:
        plac = node.first('PLAC')
        if not plac or not plac.value:
            return None

        name = plac.value.strip()
        if name in self.places:
            place = self.places[name]
        else:
            place, _ = Place.objects.get_or_create(
                tree=self.tree,
                name=name,
                defaults={'hierarchy': [p.strip() for p in name.split(',') if p.strip()]},
            )
            self.places[name] = place

        # MAP/LATI/LONG : coordonnées fournies par le fichier (« N48.856 »)
        if (map_node := plac.first('MAP')) and place.latitude is None:
            lati, long = map_node.value_of('LATI'), map_node.value_of('LONG')
            if lati and long:
                place.latitude = _coord(lati)
                place.longitude = _coord(long)
                place.geocode_provider = 'gedcom'
                place.save(update_fields=['latitude', 'longitude', 'geocode_provider'])
        return place

    def _event(self, node: Node, *, individual=None, family=None) -> Event | None:
        tag = node.tag
        if tag not in EventTag.values:
            return None

        parsed = parse_date(node.value_of('DATE'))
        is_attr = tag in ATTRIBUTE_TAGS
        # Pour un attribut, la valeur est portée par la ligne elle-même (1 OCCU Charpentier).
        value = node.value if is_attr else ''

        event = Event.objects.create(
            tree=self.tree,
            individual=individual,
            family=family,
            tag=tag,
            custom_type=node.value_of('TYPE'),
            value=value,
            place=self._place(node),
            address=node.value_of('ADDR'),
            age=node.value_of('AGE'),
            agency=node.value_of('AGNC'),
            cause=node.value_of('CAUS'),
            religion=node.value_of('RELI'),
            note=node.value_of('NOTE'),
            category=category_for(tag),
            **parsed.as_fields(),
        )
        self.counts['events'] += 1
        return event

    # ── Passes ────────────────────────────────────────────────────────────
    def _import_individual(self, node: Node) -> None:
        indi = Individual.objects.create(
            tree=self.tree,
            xref_id=node.xref,
            sex=node.value_of('SEX', Sex.UNKNOWN)[:1].upper() or Sex.UNKNOWN,
            note=node.value_of('NOTE'),
        )
        if indi.sex not in Sex.values:
            indi.sex = Sex.UNKNOWN

        for order, name_node in enumerate(node.all('NAME')):
            givn, surn = _split_name(name_node.value)
            PersonalName.objects.create(
                individual=indi,
                type=_name_type(name_node.value_of('TYPE')),
                npfx=name_node.value_of('NPFX'),
                givn=name_node.value_of('GIVN') or givn,
                nick=name_node.value_of('NICK'),
                spfx=name_node.value_of('SPFX'),
                surn=name_node.value_of('SURN') or surn,
                nsfx=name_node.value_of('NSFX'),
                is_primary=(order == 0),
                order=order,
            )

        for child in node.children:
            self._event(child, individual=indi)

        # Un décès enregistré ⇒ la personne n'est plus « vivante ».
        indi.is_living = not indi.events.filter(
            tag__in=[EventTag.DEAT, EventTag.BURI, EventTag.CREM]
        ).exists()
        indi.save(update_fields=['is_living', 'sex'])

        for obje in node.all('OBJE'):
            self._link_media(obje, individual=indi)

        self.individuals[node.xref] = indi
        self.counts['individuals'] += 1

    def _import_family(self, node: Node) -> None:
        fam = Family.objects.create(tree=self.tree, xref_id=node.xref, note=node.value_of('NOTE'))

        for role, tag in ((SpouseRole.HUSBAND, 'HUSB'), (SpouseRole.WIFE, 'WIFE')):
            for ref in node.all(tag):
                if indi := self.individuals.get(ref.value):
                    FamilySpouse.objects.get_or_create(
                        family=fam, individual=indi, defaults={'role': role},
                    )

        for order, ref in enumerate(node.all('CHIL')):
            if indi := self.individuals.get(ref.value):
                FamilyChild.objects.get_or_create(
                    family=fam, individual=indi, defaults={'order': order},
                )

        for child in node.children:
            self._event(child, family=fam)

        fam.union_type = (
            UnionType.MARRIED
            if fam.events.filter(tag=EventTag.MARR).exists()
            else UnionType.UNKNOWN
        )
        fam.save(update_fields=['union_type'])
        self.counts['families'] += 1

    def _import_media(self, node: Node) -> None:
        file_node = node.first('FILE')
        if not file_node:
            return
        media = MediaObject.objects.create(
            tree=self.tree,
            xref_id=node.xref,
            title=file_node.value_of('TITL') or node.value_of('TITL'),
            filename=file_node.value,
            mime=_mime_from(file_node),
            external_url=file_node.value if file_node.value.startswith('http') else '',
        )
        self.media[node.xref] = media
        self.counts['media'] += 1

    def _link_media(self, obje: Node, *, individual=None) -> None:
        media = self.media.get(obje.value) if obje.value.startswith('@') else None
        if media is None and (file_node := obje.first('FILE')):
            # OBJE inline (GEDCOM 5.5.1) plutôt qu'un pointeur vers un enregistrement.
            media = MediaObject.objects.create(
                tree=self.tree,
                title=obje.value_of('TITL'),
                filename=file_node.value,
                mime=_mime_from(file_node),
                external_url=file_node.value if file_node.value.startswith('http') else '',
            )
            self.counts['media'] += 1
        if media is None:
            return

        crop = obje.first('CROP')
        MediaLink.objects.create(
            media=media,
            individual=individual,
            is_primary=not MediaLink.objects.filter(individual=individual, is_primary=True).exists(),
            crop_x=_num(crop.value_of('LEFT')) if crop else None,
            crop_y=_num(crop.value_of('TOP')) if crop else None,
            crop_width=_num(crop.value_of('WIDTH')) if crop else None,
            crop_height=_num(crop.value_of('HEIGHT')) if crop else None,
        )

    def _import_source(self, node: Node) -> None:
        source = Source.objects.create(
            tree=self.tree,
            xref_id=node.xref,
            title=node.value_of('TITL') or '(sans titre)',
            author=node.value_of('AUTH'),
            publication=node.value_of('PUBL'),
            abbreviation=node.value_of('ABBR'),
            text=node.value_of('TEXT'),
        )
        self.sources[node.xref] = source
        self.counts['sources'] += 1

    def run(self, text: str) -> dict:
        roots = tokenize(text)

        for node in roots:
            if node.tag == 'OBJE':
                self._import_media(node)
            elif node.tag == 'SOUR':
                self._import_source(node)

        for node in roots:
            if node.tag == 'INDI':
                self._import_individual(node)

        for node in roots:
            if node.tag == 'FAM':
                self._import_family(node)

        return self.counts


def _split_name(value: str) -> tuple[str, str]:
    """« Jean /Dupont/ » → ("Jean", "Dupont")."""
    m = re.match(r'^(?P<givn>[^/]*)/(?P<surn>[^/]*)/?(?P<rest>.*)$', value or '')
    if not m:
        return (value or '').strip(), ''
    return m.group('givn').strip(), m.group('surn').strip()


def _name_type(raw: str) -> str:
    mapping = {
        'birth': NameType.BIRTH, 'married': NameType.MARRIED, 'maiden': NameType.MAIDEN,
        'aka': NameType.AKA, 'immigrant': NameType.IMMIGRANT,
        'professional': NameType.PROFESSIONAL, 'religious': NameType.RELIGIOUS,
    }
    return mapping.get((raw or '').strip().lower(), NameType.BIRTH)


def _mime_from(file_node: Node) -> str:
    if form := file_node.value_of('FORM'):
        if '/' in form:
            return form
        return f'image/{form.lower()}'
    ext = file_node.value.rsplit('.', 1)[-1].lower() if '.' in file_node.value else 'jpeg'
    return f'image/{"jpeg" if ext in ("jpg", "jpeg") else ext}'


def _coord(value: str) -> float | None:
    """« N48.856614 » → 48.856614 ; « W2.35 » → -2.35."""
    value = (value or '').strip().upper()
    if not value:
        return None
    sign = -1 if value[0] in 'SW' else 1
    try:
        return sign * float(value.lstrip('NSEW'))
    except ValueError:
        return None


def _num(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

class GedcomExporter:
    """Sérialise un arbre en GEDCOM 7.0 (UTF-8, terminaisons CRLF comme le veut la spec)."""

    def __init__(self, tree: Tree) -> None:
        self.tree = tree
        self.lines: list[str] = []

    def _w(self, level: int, tag: str, value: str = '', xref: str = '') -> None:
        parts = [str(level)]
        if xref:
            parts.append(xref)
        parts.append(tag)
        if value:
            parts.append(value)
        self.lines.append(' '.join(parts))

    def _text(self, level: int, tag: str, value: str) -> None:
        """Écrit une valeur potentiellement multiligne (CONT pour chaque saut de ligne)."""
        if not value:
            return
        head, *rest = value.split('\n')
        self._w(level, tag, head)
        for line in rest:
            self._w(level + 1, 'CONT', line)

    def _event(self, level: int, event: Event) -> None:
        is_attr = event.tag in ATTRIBUTE_TAGS
        self._w(level, event.tag, event.value if is_attr else '')
        if event.custom_type:
            self._w(level + 1, 'TYPE', event.custom_type)
        if date := format_date(event):
            self._w(level + 1, 'DATE', date)
        if event.place:
            self._w(level + 1, 'PLAC', event.place.name)
            if event.place.latitude is not None and event.place.longitude is not None:
                self._w(level + 2, 'MAP')
                lat, lon = float(event.place.latitude), float(event.place.longitude)
                self._w(level + 3, 'LATI', f'{"N" if lat >= 0 else "S"}{abs(lat):.6f}')
                self._w(level + 3, 'LONG', f'{"E" if lon >= 0 else "W"}{abs(lon):.6f}')
        if event.age:
            self._w(level + 1, 'AGE', event.age)
        if event.cause:
            self._w(level + 1, 'CAUS', event.cause)
        if event.agency:
            self._w(level + 1, 'AGNC', event.agency)
        self._text(level + 1, 'NOTE', event.note)

    def run(self) -> str:
        tree = self.tree
        now = dt.datetime.now(dt.timezone.utc)

        self._w(0, 'HEAD')
        self._w(1, 'GEDC')
        self._w(2, 'VERS', '7.0')
        self._w(1, 'SOUR', 'ARBRE-GENEALOGIQUE')
        self._w(2, 'NAME', tree.source_software)
        self._w(1, 'DATE', now.strftime('%d %b %Y').upper())
        self._w(2, 'TIME', now.strftime('%H:%M:%S'))
        self._w(1, 'LANG', tree.language or 'fr')
        if tree.copyright:
            self._w(1, 'COPR', tree.copyright)
        if tree.submitter_name:
            self._w(1, 'SUBM', '@SUB1@')

        individuals = (
            tree.individuals
            .prefetch_related('names', 'events__place', 'media_links__media',
                              'families_as_spouse__family', 'families_as_child__family')
            .all()
        )

        # Les xref doivent être stables : on réutilise ceux de l'import quand ils existent.
        indi_xref = {i.pk: i.xref_id or f'@I{i.pk}@' for i in individuals}
        families = tree.families.prefetch_related('spouses', 'children', 'events__place').all()
        fam_xref = {f.pk: f.xref_id or f'@F{f.pk}@' for f in families}
        media_xref = {m.pk: m.xref_id or f'@M{m.pk}@' for m in tree.media.all()}

        for indi in individuals:
            self._w(0, 'INDI', xref=indi_xref[indi.pk])
            for name in indi.names.all():
                self._w(1, 'NAME', name.gedcom_value)
                if name.type != NameType.BIRTH:
                    self._w(2, 'TYPE', name.type.lower())
                for tag, val in (('NPFX', name.npfx), ('GIVN', name.givn), ('NICK', name.nick),
                                 ('SPFX', name.spfx), ('SURN', name.surn), ('NSFX', name.nsfx)):
                    if val:
                        self._w(2, tag, val)
            if indi.sex:
                self._w(1, 'SEX', indi.sex)
            for event in indi.events.all():
                self._event(1, event)
            for link in indi.families_as_child.all():
                self._w(1, 'FAMC', fam_xref[link.family_id])
                if link.pedigree != Pedigree.BIRTH:
                    self._w(2, 'PEDI', link.pedigree.lower())
                if link.status:
                    self._w(2, 'STAT', link.status.lower())
            for link in indi.families_as_spouse.all():
                self._w(1, 'FAMS', fam_xref[link.family_id])
            for link in indi.media_links.all():
                self._w(1, 'OBJE', media_xref[link.media_id])
            self._text(1, 'NOTE', indi.note)

        for fam in families:
            self._w(0, 'FAM', xref=fam_xref[fam.pk])
            for spouse in fam.spouses.all():
                tag = 'HUSB' if spouse.role == SpouseRole.HUSBAND else (
                    'WIFE' if spouse.role == SpouseRole.WIFE else 'HUSB'
                )
                self._w(1, tag, indi_xref[spouse.individual_id])
            for child in fam.children.all():
                self._w(1, 'CHIL', indi_xref[child.individual_id])
            for event in fam.events.all():
                self._event(1, event)
            self._text(1, 'NOTE', fam.note)

        for media in tree.media.all():
            self._w(0, 'OBJE', xref=media_xref[media.pk])
            self._w(1, 'FILE', media.external_url or media.filename or f'media-{media.pk}')
            self._w(2, 'FORM', media.mime)
            if media.title:
                self._w(2, 'TITL', media.title)

        for source in tree.sources.all():
            self._w(0, 'SOUR', xref=source.xref_id or f'@S{source.pk}@')
            self._text(1, 'TITL', source.title)
            if source.author:
                self._w(1, 'AUTH', source.author)
            if source.publication:
                self._w(1, 'PUBL', source.publication)
            if source.external_url:
                self._w(1, 'WWW', source.external_url)

        if tree.submitter_name:
            self._w(0, 'SUBM', xref='@SUB1@')
            self._w(1, 'NAME', tree.submitter_name)

        self._w(0, 'TRLR')
        return '\r\n'.join(self.lines) + '\r\n'
