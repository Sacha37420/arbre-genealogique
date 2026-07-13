import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Observable, forkJoin, of } from 'rxjs';

import { ApiService } from '../../core/api.service';
import {
  CATEGORY_LABELS,
  EventRecord,
  EventType,
  IndividualDetail,
  Sex,
} from '../../core/models';
import { describeError } from '../../core/tree-store';
import { formatGedcomDate } from './card-fields';

/**
 * Un événement en cours d'édition.
 *
 * Les créations reçoivent un id négatif temporaire : c'est ce qui permet de les
 * distinguer des événements déjà en base au moment d'enregistrer, sans les avoir
 * déjà envoyés au serveur.
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

let tempId = -1;

/**
 * Édition complète d'une personne : identité, événements et périodes.
 *
 * Rien n'est envoyé au fil de la frappe : les modifications sont accumulées et
 * partent en un seul « Enregistrer ». Une fiche généalogique se relit et se
 * corrige d'un bloc, et un envoi par frappe multiplierait les allers-retours pour
 * un résultat plus fragile.
 *
 * La date se saisit dans la syntaxe GEDCOM (« ABT 1875 », « FROM 1901 TO 1918 ») :
 * c'est ce qui permet de dire « vers », « avant » ou « entre » sans mentir sur la
 * précision de la source. Une traduction en clair s'affiche sous le champ, et une
 * date en intervalle devient automatiquement une période sur la frise.
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

  readonly closed = output<void>();
  readonly saved = output<void>();

  readonly detail = signal<IndividualDetail | null>(null);
  readonly events = signal<DraftEvent[]>([]);
  readonly eventTypes = signal<EventType[]>([]);

  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly error = signal('');

  // ── Identité ──────────────────────────────────────────────────────────────
  readonly givn = signal('');
  readonly surn = signal('');
  readonly nick = signal('');
  readonly sex = signal<Sex>('U');
  readonly note = signal('');
  readonly confidential = signal(false);

  readonly sexOptions: { value: Sex; label: string }[] = [
    { value: 'M', label: 'Masculin' },
    { value: 'F', label: 'Féminin' },
    { value: 'X', label: 'Autre / intersexe' },
    { value: 'U', label: 'Inconnu' },
  ];

  readonly categoryOptions = Object.entries(CATEGORY_LABELS).map(([key, label]) => ({ key, label }));

  readonly individualTypes = computed(() =>
    this.eventTypes().filter((t) => t.scope === 'INDIVIDUAL'),
  );
  readonly familyTypes = computed(() => this.eventTypes().filter((t) => t.scope === 'FAMILY'));

  readonly visibleEvents = computed(() => this.events().filter((e) => !e.deleted));

  readonly personalEvents = computed(() => this.visibleEvents().filter((e) => e.individual !== null));
  readonly familyEvents = computed(() => this.visibleEvents().filter((e) => e.family !== null));

  readonly hasFamily = computed(() => (this.detail()?.spouse_families.length ?? 0) > 0);

  readonly dirty = computed(
    () =>
      this.identityDirty() ||
      this.events().some((e) => e.dirty || e.deleted || e.id < 0),
  );

  private readonly identityDirty = signal(false);

  constructor() {
    this.api.getEventTypes().subscribe({
      next: (types) => this.eventTypes.set(types),
      error: (err) => this.error.set(describeError(err)),
    });

    effect(() => this.load(this.individualId()));
  }

  private load(id: number): void {
    this.loading.set(true);
    this.error.set('');

    this.api.getIndividual(id).subscribe({
      next: (detail) => {
        this.detail.set(detail);
        this.givn.set(detail.given_name);
        this.surn.set(detail.surname);
        this.nick.set(detail.nickname);
        this.sex.set(detail.sex);
        this.note.set(detail.note);
        this.confidential.set(detail.confidential);
        this.identityDirty.set(false);

        const drafts = detail.events.map((e) => this.toDraft(e));

        // Les mariages et divorces appartiennent à la famille, pas à la personne :
        // ils figurent pourtant sur sa frise, donc ils doivent être éditables ici.
        const familyCalls = detail.spouse_families.map((familyId) =>
          this.api.getFamilyEvents(familyId),
        );

        if (!familyCalls.length) {
          this.events.set(drafts);
          this.loading.set(false);
          return;
        }

        forkJoin(familyCalls).subscribe({
          next: (groups) => {
            this.events.set([...drafts, ...groups.flat().map((e) => this.toDraft(e))]);
            this.loading.set(false);
          },
          error: (err) => {
            this.events.set(drafts);
            this.loading.set(false);
            this.error.set(describeError(err));
          },
        });
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(describeError(err));
      },
    });
  }

  private toDraft(event: EventRecord): DraftEvent {
    return {
      ...event,
      dirty: false,
      deleted: false,
      expanded: false,
    };
  }

  // ── Identité ──────────────────────────────────────────────────────────────
  touchIdentity(): void {
    this.identityDirty.set(true);
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

  addEvent(scope: 'INDIVIDUAL' | 'FAMILY'): void {
    const detail = this.detail();
    if (!detail) return;

    const family = scope === 'FAMILY' ? (detail.spouse_families[0] ?? null) : null;
    if (scope === 'FAMILY' && family === null) return;

    const type = (scope === 'FAMILY' ? this.familyTypes() : this.individualTypes())[0];

    this.events.update((list) => [
      ...list,
      {
        id: tempId--,
        tree: detail.tree,
        individual: scope === 'INDIVIDUAL' ? detail.id : null,
        family,
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

  removeEvent(id: number): void {
    // Une création jamais envoyée disparaît ; un événement existant est marqué
    // pour suppression, effective à l'enregistrement.
    if (id < 0) {
      this.events.update((list) => list.filter((e) => e.id !== id));
      return;
    }
    this.events.update((list) =>
      list.map((e) => (e.id === id ? { ...e, deleted: true } : e)),
    );
  }

  /** Le type dicte la catégorie de frise et le fait qu'il porte une valeur. */
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

  /** Traduction en clair de la date GEDCOM saisie, affichée sous le champ. */
  dateHint(event: DraftEvent): string {
    if (!event.date_raw.trim()) return '';
    return formatGedcomDate(event.date_raw);
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

    forkJoin(calls.length ? calls : [of(null)]).subscribe({
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
