import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Observable, forkJoin } from 'rxjs';

import { ApiService } from '../../core/api.service';
import {
  CATEGORY_LABELS,
  EventRecord,
  EventType,
  IndividualDetail,
  PEDIGREE_TYPES,
  PersonCard,
  Relations,
  Sex,
  SpouseFamily,
  UNION_END_TAGS,
  UNION_START_TAGS,
  UNION_TYPES,
} from '../../core/models';
import { describeError } from '../../core/tree-store';
import { formatGedcomDate } from './card-fields';

/**
 * Un événement en cours d'édition.
 *
 * Les créations reçoivent un id négatif temporaire : c'est ce qui permet de les
 * distinguer des événements déjà en base au moment d'enregistrer, sans avoir eu
 * à les envoyer au serveur d'abord.
 */
interface DraftEvent extends Partial<EventRecord> {
  id: number;
  tag: string;
  custom_type: string;
  value: string;
  date_raw: string;
  place_name: string;
  age: string;
  cause: string;
  agency: string;
  note: string;
  category: string;
  individual: number | null;
  family: number | null;
  dirty: boolean;
  deleted: boolean;
  expanded: boolean;
}

type Relation = 'PARENT' | 'SPOUSE' | 'CHILD' | 'SIBLING';

let tempId = -1;

/**
 * Édition complète d'une personne : identité, relations, événements et périodes.
 *
 * Deux régimes d'enregistrement, et c'est délibéré :
 *   · l'identité et les événements sont accumulés puis envoyés en un seul
 *     « Enregistrer » — on relit et corrige une fiche d'un bloc ;
 *   · les **relations** partent immédiatement, car ajouter un parent crée une
 *     personne et une famille : différer ces créations rendrait l'écran menteur
 *     (on verrait un lien qui n'existe pas encore) et la reprise sur erreur bien
 *     plus fragile.
 */
@Component({
  selector: 'app-person-editor',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './person-editor.component.html',
  styleUrl: './person-editor.component.scss',
})
export class PersonEditorComponent {
  private api = inject(ApiService);

  readonly individualId = input.required<number>();
  /** Personnes déjà dans l'arbre : on peut rattacher l'une d'elles plutôt que d'en créer une. */
  readonly people = input<PersonCard[]>([]);

  readonly closed = output<void>();
  readonly saved = output<void>();
  /** Une relation a changé : l'arbre doit être redessiné, sans fermer l'éditeur. */
  readonly relationsChanged = output<void>();

  readonly detail = signal<IndividualDetail | null>(null);
  readonly relations = signal<Relations | null>(null);
  readonly events = signal<DraftEvent[]>([]);
  readonly eventTypes = signal<EventType[]>([]);

  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly busy = signal('');
  readonly error = signal('');

  // ── Identité ──────────────────────────────────────────────────────────────
  readonly givn = signal('');
  readonly surn = signal('');
  readonly nick = signal('');
  readonly sex = signal<Sex>('U');
  readonly note = signal('');
  readonly confidential = signal(false);
  private readonly identityDirty = signal(false);

  // ── Ajout d'un proche ─────────────────────────────────────────────────────
  /** Formulaire ouvert pour quelle relation ? (une seule à la fois) */
  readonly adding = signal<Relation | null>(null);
  readonly pickedPerson = signal<number | null>(null);
  readonly newGivn = signal('');
  readonly newSurn = signal('');
  readonly newSex = signal<Sex>('U');

  /** Modifications du type d'union, envoyées avec le reste. */
  private readonly familyPatches = signal<Record<number, string>>({});

  readonly sexOptions: { value: Sex; label: string }[] = [
    { value: 'M', label: 'Masculin' },
    { value: 'F', label: 'Féminin' },
    { value: 'X', label: 'Autre / intersexe' },
    { value: 'U', label: 'Inconnu' },
  ];
  readonly unionTypes = UNION_TYPES;
  readonly pedigreeTypes = PEDIGREE_TYPES;
  readonly categoryOptions = Object.entries(CATEGORY_LABELS).map(([key, label]) => ({ key, label }));

  readonly startTagOptions = [
    { value: 'MARR', label: 'Mariage' },
    { value: 'ENGA', label: 'Fiançailles' },
  ];
  readonly endTagOptions = [
    { value: '', label: 'Union en cours' },
    { value: 'DIV', label: 'Divorce' },
    { value: 'SEPA', label: 'Séparation' },
    { value: 'ANUL', label: 'Annulation' },
  ];

  readonly individualTypes = computed(() =>
    this.eventTypes().filter((t) => t.scope === 'INDIVIDUAL'),
  );
  readonly familyTypes = computed(() => this.eventTypes().filter((t) => t.scope === 'FAMILY'));

  readonly visibleEvents = computed(() => this.events().filter((e) => !e.deleted));
  readonly personalEvents = computed(() => this.visibleEvents().filter((e) => e.individual !== null));

  /** Personnes rattachables : tout l'arbre sauf la personne éditée. */
  readonly candidates = computed(() =>
    this.people()
      .filter((p) => p.id !== this.individualId())
      .map((p) => ({
        id: p.id,
        label: `${p.full_name || 'Sans nom'}${p.birth_year ? ` (${p.birth_year})` : ''}`,
      })),
  );

  readonly dirty = computed(
    () =>
      this.identityDirty() ||
      Object.keys(this.familyPatches()).length > 0 ||
      this.events().some((e) => e.dirty || e.deleted || e.id < 0),
  );

  constructor() {
    this.api.getEventTypes().subscribe({
      next: (types) => this.eventTypes.set(types),
      error: (err) => this.error.set(describeError(err)),
    });

    effect(() => this.load(this.individualId()));
  }

  // ── Chargement ────────────────────────────────────────────────────────────
  private load(id: number): void {
    this.loading.set(true);
    this.error.set('');

    forkJoin({
      detail: this.api.getIndividual(id),
      relations: this.api.getRelations(id),
    }).subscribe({
      next: ({ detail, relations }) => {
        this.detail.set(detail);
        this.relations.set(relations);

        this.givn.set(detail.given_name);
        this.surn.set(detail.surname);
        this.nick.set(detail.nickname);
        this.sex.set(detail.sex);
        this.note.set(detail.note);
        this.confidential.set(detail.confidential);
        this.identityDirty.set(false);
        this.familyPatches.set({});

        // Les événements de famille viennent des relations : ce sont ceux du couple.
        const familyEvents = relations.spouse_families.flatMap((f) => f.events);
        this.events.set([...detail.events, ...familyEvents].map((e) => this.toDraft(e)));
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(describeError(err));
      },
    });
  }

  /** Recharge les relations après une modification structurelle, sans perdre la saisie. */
  private reloadRelations(): void {
    this.api.getRelations(this.individualId()).subscribe({
      next: (relations) => {
        this.relations.set(relations);

        // Les événements de famille ont pu changer de famille : on ne conserve que
        // les brouillons non enregistrés, et on reprend le reste du serveur.
        const drafts = this.events().filter((e) => e.id < 0 || e.dirty || e.deleted);
        const fresh = relations.spouse_families
          .flatMap((f) => f.events)
          .filter((e) => !drafts.some((d) => d.id === e.id))
          .map((e) => this.toDraft(e));
        const personal = this.events().filter((e) => e.individual !== null);

        this.events.set([
          ...personal,
          ...fresh,
          ...drafts.filter((d) => d.family !== null),
        ]);
        this.busy.set('');
        this.relationsChanged.emit();
      },
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  private toDraft(event: EventRecord): DraftEvent {
    return { ...event, dirty: false, deleted: false, expanded: false };
  }

  // ── Identité ──────────────────────────────────────────────────────────────
  touchIdentity(): void {
    this.identityDirty.set(true);
  }

  // ── Relations ─────────────────────────────────────────────────────────────
  openAdd(relation: Relation): void {
    this.adding.set(this.adding() === relation ? null : relation);
    this.pickedPerson.set(null);
    this.newGivn.set('');
    this.newSurn.set('');
    this.newSex.set(relation === 'PARENT' ? 'U' : 'U');
  }

  confirmAdd(relation: Relation): void {
    const existing = this.pickedPerson();
    const payload = existing
      ? { relation, individual: existing }
      : {
          relation,
          givn: this.newGivn().trim(),
          surn: this.newSurn().trim(),
          sex: this.newSex(),
        };

    if (!existing && !payload.givn && !payload.surn) {
      this.error.set('Choisissez une personne existante ou saisissez un nom.');
      return;
    }

    this.busy.set('Ajout du lien…');
    this.error.set('');

    this.api.addRelative(this.individualId(), payload).subscribe({
      next: () => {
        this.adding.set(null);
        this.reloadRelations();
      },
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  removeSpouse(linkId: number): void {
    this.busy.set('Suppression du lien…');
    this.api.removeSpouseLink(linkId).subscribe({
      next: () => this.reloadRelations(),
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  removeChild(linkId: number): void {
    this.busy.set('Suppression du lien…');
    this.api.removeChildLink(linkId).subscribe({
      next: () => this.reloadRelations(),
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  /** Le type de filiation (adopté, accueil…) est structurel : il part immédiatement. */
  changePedigree(linkId: number, pedigree: string): void {
    this.busy.set('Mise à jour…');
    this.api.updateChildLink(linkId, { pedigree }).subscribe({
      next: () => this.reloadRelations(),
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  // ── Union : type, début, fin ───────────────────────────────────────────────
  unionType(family: SpouseFamily): string {
    return this.familyPatches()[family.family] ?? family.union_type;
  }

  setUnionType(family: SpouseFamily, value: string): void {
    this.familyPatches.update((patches) => ({ ...patches, [family.family]: value }));
  }

  startEvent(family: SpouseFamily): DraftEvent | null {
    return (
      this.visibleEvents().find(
        (e) => e.family === family.family && UNION_START_TAGS.includes(e.tag),
      ) ?? null
    );
  }

  endEvent(family: SpouseFamily): DraftEvent | null {
    return (
      this.visibleEvents().find(
        (e) => e.family === family.family && UNION_END_TAGS.includes(e.tag),
      ) ?? null
    );
  }

  /** Événements du couple qui ne sont ni son début ni sa fin (bans, contrat…). */
  otherFamilyEvents(family: SpouseFamily): DraftEvent[] {
    return this.visibleEvents().filter(
      (e) =>
        e.family === family.family &&
        !UNION_START_TAGS.includes(e.tag) &&
        !UNION_END_TAGS.includes(e.tag),
    );
  }

  setStart(family: SpouseFamily, changes: { tag?: string; date_raw?: string }): void {
    const existing = this.startEvent(family);
    if (existing) {
      this.patchEvent(existing.id, changes);
      return;
    }
    this.addFamilyEvent(family, changes.tag ?? 'MARR', changes.date_raw ?? '');
  }

  setEndTag(family: SpouseFamily, tag: string): void {
    const existing = this.endEvent(family);

    // « Union en cours » : la fin est retirée.
    if (!tag) {
      if (existing) this.removeEvent(existing.id);
      return;
    }
    if (existing) {
      this.patchEvent(existing.id, { tag, category: 'FAMILY' });
      return;
    }
    this.addFamilyEvent(family, tag, '');
  }

  setEndDate(family: SpouseFamily, date: string): void {
    const existing = this.endEvent(family);
    if (existing) {
      this.patchEvent(existing.id, { date_raw: date });
      return;
    }
    // Une date de fin saisie sans avoir choisi la nature : on suppose un divorce.
    this.addFamilyEvent(family, 'DIV', date);
  }

  private addFamilyEvent(family: SpouseFamily, tag: string, date_raw: string): void {
    const detail = this.detail();
    if (!detail) return;

    this.events.update((list) => [
      ...list,
      {
        id: tempId--,
        tree: detail.tree,
        individual: null,
        family: family.family,
        tag,
        custom_type: '',
        value: '',
        date_raw,
        place_name: '',
        age: '',
        cause: '',
        agency: '',
        note: '',
        category: 'FAMILY',
        is_period: false,
        is_attribute: false,
        label: '',
        sort_order: 0,
        dirty: true,
        deleted: false,
        expanded: false,
      },
    ]);
  }

  /** Résumé lisible de la période d'union, affiché sous le couple. */
  unionSpan(family: SpouseFamily): string {
    const start = this.startEvent(family)?.date_raw;
    const end = this.endEvent(family);

    if (!start && !end?.date_raw) return '';
    const from = start ? formatGedcomDate(start) : '?';
    if (!end) return `depuis ${from}`;

    const kind = this.endTagOptions.find((o) => o.value === end.tag)?.label ?? 'fin';
    const to = end.date_raw ? formatGedcomDate(end.date_raw) : '?';
    return `${from} → ${to} (${kind.toLowerCase()})`;
  }

  // ── Événements ────────────────────────────────────────────────────────────
  patchEvent(id: number, changes: Partial<DraftEvent>): void {
    this.events.update((list) =>
      list.map((e) => (e.id === id ? { ...e, ...changes, dirty: true } : e)),
    );
  }

  toggleExpanded(id: number): void {
    this.events.update((list) =>
      list.map((e) => (e.id === id ? { ...e, expanded: !e.expanded } : e)),
    );
  }

  addEvent(): void {
    const detail = this.detail();
    if (!detail) return;

    const type = this.individualTypes()[0];
    this.events.update((list) => [
      ...list,
      {
        id: tempId--,
        tree: detail.tree,
        individual: detail.id,
        family: null,
        tag: type?.tag ?? 'EVEN',
        custom_type: '',
        value: '',
        date_raw: '',
        place_name: '',
        age: '',
        cause: '',
        agency: '',
        note: '',
        category: type?.category ?? 'OTHER',
        is_period: false,
        is_attribute: type?.is_attribute ?? false,
        label: type?.label ?? '',
        sort_order: 0,
        dirty: true,
        deleted: false,
        expanded: true,
      },
    ]);
  }

  addOtherFamilyEvent(family: SpouseFamily): void {
    this.addFamilyEvent(family, 'MARB', '');
  }

  removeEvent(id: number): void {
    // Une création jamais envoyée disparaît ; un événement existant est marqué pour
    // suppression, effective à l'enregistrement.
    if (id < 0) {
      this.events.update((list) => list.filter((e) => e.id !== id));
      return;
    }
    this.events.update((list) => list.map((e) => (e.id === id ? { ...e, deleted: true } : e)));
  }

  changeTag(id: number, tag: string): void {
    const type = this.eventTypes().find((t) => t.tag === tag);
    this.patchEvent(id, {
      tag,
      category: type?.category ?? 'OTHER',
      is_attribute: type?.is_attribute ?? false,
      label: type?.label ?? '',
    });
  }

  typesFor(event: DraftEvent): EventType[] {
    return event.family !== null ? this.familyTypes() : this.individualTypes();
  }

  isAttribute(event: DraftEvent): boolean {
    return this.eventTypes().find((t) => t.tag === event.tag)?.is_attribute ?? false;
  }

  isCustom(event: DraftEvent): boolean {
    return event.tag === 'EVEN' || event.tag === 'FACT';
  }

  dateHint(event: DraftEvent): string {
    return event.date_raw.trim() ? formatGedcomDate(event.date_raw) : '';
  }

  hint(raw: string): string {
    return raw?.trim() ? formatGedcomDate(raw) : '';
  }

  /** Un intervalle (FROM…TO, BET…AND) devient une période sur la frise. */
  looksLikePeriod(event: DraftEvent): boolean {
    return /^(FROM\s+.+\s+TO\s+|BET\s+.+\s+AND\s+)/i.test(event.date_raw.trim());
  }

  // ── Enregistrement ────────────────────────────────────────────────────────
  save(): void {
    const detail = this.detail();
    if (!detail || this.saving()) return;

    this.saving.set(true);
    this.error.set('');

    const calls: Observable<unknown>[] = [];

    if (this.identityDirty()) {
      calls.push(
        this.api.updateIndividual(detail.id, {
          givn: this.givn(),
          surn: this.surn(),
          nick: this.nick(),
          sex: this.sex(),
          note: this.note(),
          confidential: this.confidential(),
        }),
      );
    }

    for (const [family, unionType] of Object.entries(this.familyPatches())) {
      calls.push(this.api.updateFamily(Number(family), { union_type: unionType }));
    }

    for (const event of this.events()) {
      if (event.deleted) {
        if (event.id > 0) calls.push(this.api.deleteEvent(event.id));
      } else if (event.id < 0) {
        calls.push(this.api.createEvent(this.payload(event, detail.tree)));
      } else if (event.dirty) {
        calls.push(this.api.updateEvent(event.id, this.payload(event, detail.tree)));
      }
    }

    if (!calls.length) {
      this.saving.set(false);
      this.closed.emit();
      return;
    }

    forkJoin(calls).subscribe({
      next: () => {
        this.saving.set(false);
        this.saved.emit();
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(describeError(err));
      },
    });
  }

  private payload(event: DraftEvent, tree: number): Record<string, unknown> {
    return {
      tree,
      individual: event.individual,
      family: event.family,
      tag: event.tag,
      custom_type: event.custom_type,
      value: event.value,
      date_raw: event.date_raw,
      place_name: event.place_name,
      age: event.age,
      cause: event.cause,
      agency: event.agency,
      note: event.note,
      category: event.category,
    };
  }
}
