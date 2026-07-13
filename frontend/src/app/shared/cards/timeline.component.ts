import { Component, computed, input, output, signal } from '@angular/core';

import { CATEGORY_COLORS, CATEGORY_LABELS, Timeline, TimelineEntry } from '../../core/models';
import { formatGedcomDate } from './card-fields';

interface PlacedPeriod {
  entry: TimelineEntry;
  left: number;
  width: number;
  lane: number;
  color: string;
}

interface PlacedEvent {
  entry: TimelineEntry;
  left: number;
  color: string;
}

/**
 * Frise chronologique d'une vie.
 *
 * Deux registres cohabitent : les **périodes** (FROM…TO — une résidence, un métier,
 * un service militaire) dessinées comme des barres, et les **événements** ponctuels
 * (naissance, mariage, décès) comme des points sur l'axe. Les deux sont cliquables
 * et ouvrent leur détail : c'est ce que demande la grande carte.
 *
 * Les événements sans date sont relégués en bas plutôt que placés arbitrairement —
 * les inventer sur l'axe serait mentir sur la source.
 */
@Component({
  selector: 'app-timeline',
  standalone: true,
  styleUrl: './timeline.component.scss',
  template: `
    @if (timeline(); as data) {
      @if (span(); as bounds) {
        <div class="frise">
          <div class="axis">
            @for (tick of ticks(); track tick) {
              <div class="tick" [style.left.%]="position(tick)">
                <span class="tick-label">{{ tick }}</span>
              </div>
            }
          </div>

          <div class="lanes" [style.height.px]="laneCount() * 26 + 8">
            @for (period of periods(); track period.entry.id) {
              <button
                type="button"
                class="period"
                [class.active]="selected()?.id === period.entry.id"
                [style.left.%]="period.left"
                [style.width.%]="period.width"
                [style.top.px]="period.lane * 26"
                [style.background]="period.color"
                (click)="select(period.entry)"
                [title]="period.entry.label + ' — ' + dateText(period.entry)"
              >
                <span>{{ period.entry.value || period.entry.label }}</span>
              </button>
            }
          </div>

          <div class="events">
            @for (event of events(); track event.entry.id) {
              <button
                type="button"
                class="event"
                [class.active]="selected()?.id === event.entry.id"
                [style.left.%]="event.left"
                [style.border-color]="event.color"
                (click)="select(event.entry)"
                [title]="event.entry.label + ' — ' + dateText(event.entry)"
              >
                <span class="dot" [style.background]="event.color"></span>
                <span class="event-label">{{ event.entry.label }}</span>
              </button>
            }
          </div>
        </div>
      } @else {
        <p class="empty">Aucune date connue : ajoutez une naissance ou un décès pour voir la frise.</p>
      }

      @if (undated().length) {
        <div class="undated">
          <span class="undated-title">Sans date</span>
          @for (entry of undated(); track entry.id) {
            <button
              type="button"
              class="chip"
              [class.active]="selected()?.id === entry.id"
              [style.border-color]="color(entry)"
              (click)="select(entry)"
            >
              {{ entry.label }}
            </button>
          }
        </div>
      }

      @if (selected(); as entry) {
        <div class="detail">
          <div class="detail-head">
            <span class="badge" [style.background]="color(entry)">{{ categoryLabel(entry) }}</span>
            <strong>{{ entry.label }}</strong>
            <button type="button" class="close" (click)="select(null)" aria-label="Fermer">×</button>
          </div>

          <dl>
            @if (dateText(entry)) {
              <dt>Date</dt>
              <dd>{{ dateText(entry) }}</dd>
            }
            @if (entry.value) {
              <dt>Valeur</dt>
              <dd>{{ entry.value }}</dd>
            }
            @if (entry.place) {
              <dt>Lieu</dt>
              <dd>{{ entry.place }}</dd>
            }
            @if (entry.age) {
              <dt>Âge</dt>
              <dd>{{ entry.age }}</dd>
            }
            @if (entry.cause) {
              <dt>Cause</dt>
              <dd>{{ entry.cause }}</dd>
            }
            @if (entry.agency) {
              <dt>Organisme</dt>
              <dd>{{ entry.agency }}</dd>
            }
            @if (entry.note) {
              <dt>Note</dt>
              <dd>{{ entry.note }}</dd>
            }
          </dl>

          @if (entry.related_individual) {
            <button type="button" class="link" (click)="openRelated.emit(entry.related_individual!)">
              Voir cette personne →
            </button>
          }
        </div>
      }
    }
  `,
})
export class TimelineComponent {
  readonly timeline = input<Timeline | null>(null);
  /** Vide = toutes les catégories ; sinon, filtre imposé par le gabarit. */
  readonly categories = input<string[]>([]);
  readonly showPeriods = input(true);

  readonly openRelated = output<number>();

  readonly selected = signal<TimelineEntry | null>(null);

  private readonly visible = computed(() => {
    const entries = this.timeline()?.entries ?? [];
    const filter = this.categories();
    return filter.length ? entries.filter((e) => filter.includes(e.category)) : entries;
  });

  readonly span = computed(() => {
    const years = this.visible()
      .flatMap((e) => [e.start_year, e.end_year])
      .filter((y): y is number => y !== null);
    if (!years.length) return null;

    const from = Math.min(...years);
    const to = Math.max(...years);
    // Une vie qui tient sur une seule année ne peut pas s'étaler : on ouvre l'axe.
    return to === from ? { from: from - 1, to: to + 1 } : { from, to };
  });

  readonly ticks = computed(() => {
    const bounds = this.span();
    if (!bounds) return [];

    const range = bounds.to - bounds.from;
    const step = range <= 12 ? 2 : range <= 30 ? 5 : range <= 80 ? 10 : 20;
    const start = Math.ceil(bounds.from / step) * step;

    const ticks: number[] = [];
    for (let year = start; year <= bounds.to; year += step) ticks.push(year);
    return ticks;
  });

  readonly periods = computed<PlacedPeriod[]>(() => {
    if (!this.showPeriods()) return [];

    const candidates = this.visible().filter(
      (e) => e.is_period && e.start_year !== null && e.end_year !== null,
    );

    // Empilement en couloirs : deux périodes qui se chevauchent ne peuvent pas
    // partager une ligne, sinon l'une masquerait l'autre.
    const laneEnds: number[] = [];
    return candidates.map((entry) => {
      const left = this.position(entry.start_year!);
      const width = Math.max(this.position(entry.end_year!) - left, 1.5);

      let lane = laneEnds.findIndex((end) => left > end + 1);
      if (lane === -1) lane = laneEnds.length;
      laneEnds[lane] = left + width;

      return { entry, left, width, lane, color: this.color(entry) };
    });
  });

  readonly laneCount = computed(() => {
    const lanes = this.periods().map((p) => p.lane);
    return lanes.length ? Math.max(...lanes) + 1 : 0;
  });

  readonly events = computed<PlacedEvent[]>(() =>
    this.visible()
      .filter((e) => !e.is_period && e.start_year !== null)
      .map((entry) => ({
        entry,
        left: this.position(entry.start_year!),
        color: this.color(entry),
      })),
  );

  readonly undated = computed(() => this.visible().filter((e) => e.start_year === null));

  position(year: number): number {
    const bounds = this.span();
    if (!bounds) return 0;
    const range = bounds.to - bounds.from || 1;
    return Math.min(Math.max(((year - bounds.from) / range) * 100, 0), 100);
  }

  color(entry: TimelineEntry): string {
    return entry.color || CATEGORY_COLORS[entry.category] || CATEGORY_COLORS['OTHER'];
  }

  categoryLabel(entry: TimelineEntry): string {
    return CATEGORY_LABELS[entry.category] ?? entry.category;
  }

  dateText(entry: TimelineEntry): string {
    if (entry.date_raw) return formatGedcomDate(entry.date_raw);
    return entry.date_phrase;
  }

  select(entry: TimelineEntry | null): void {
    this.selected.update((current) => (current?.id === entry?.id ? null : entry));
  }
}
