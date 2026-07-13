import { NgStyle } from '@angular/common';
import { Component, computed, inject, input, output } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { CardTemplate, PersonCard, Timeline } from '../../core/models';
import { cropStyle, initials, resolveFields } from './card-fields';
import { TimelineComponent } from './timeline.component';

/**
 * La grande carte, ouverte au clic sur une mini-carte.
 *
 * Disposition par défaut : photo en haut à gauche, mêmes informations que la
 * mini-carte à sa droite, et en dessous la frise chronologique de la vie, dont
 * chaque période et chaque événement s'ouvre au clic. Le gabarit FULL commande
 * tout : champs, photo, présence et contenu de la frise.
 */
@Component({
  selector: 'app-full-card',
  standalone: true,
  imports: [NgStyle, TimelineComponent],
  styleUrl: './full-card.component.scss',
  template: `
    <article class="full" [style.background]="template()?.background_color || '#fff'">
      <header>
        @if (template()?.photo_position !== 'NONE') {
          <div class="photo" [ngStyle]="photoStyle()">
            @if (card().photo_url) {
              <img [src]="photoSrc()" [alt]="card().full_name" [ngStyle]="crop()" />
            } @else if (template()?.photo_placeholder === 'initials') {
              <span class="initials" [style.color]="card().style.accent_color">{{ initials() }}</span>
            } @else {
              <span class="silhouette" aria-hidden="true">👤</span>
            }
          </div>
        }

        <div class="identity">
          @for (field of fields(); track field.key) {
            <div class="row">
              <span class="label">{{ field.label }}</span>
              <span
                class="value"
                [style.font-weight]="field.bold ? 700 : 400"
                [style.font-size.px]="field.size"
                [style.color]="field.color"
                [style.text-transform]="field.uppercase ? 'uppercase' : 'none'"
                >{{ field.value }}</span
              >
            </div>
          }
        </div>

        <button type="button" class="close" (click)="closed.emit()" aria-label="Fermer">×</button>
      </header>

      @if (template()?.show_timeline) {
        <section class="timeline-section">
          <h3>Frise chronologique</h3>
          @if (timeline()) {
            <app-timeline
              [timeline]="timeline()"
              [categories]="template()?.timeline_categories ?? []"
              [showPeriods]="template()?.show_periods ?? true"
              (openRelated)="openRelated.emit($event)"
            />
          } @else {
            <p class="loading">Chargement de la frise…</p>
          }
        </section>
      }

      <footer>
        <button type="button" class="btn ghost" (click)="addPhoto.emit(card())">
          {{ card().has_photo ? 'Changer la photo' : 'Ajouter une photo' }}
        </button>
        <button type="button" class="btn ghost" (click)="enrich.emit(card())">
          Compléter depuis une source externe
        </button>
      </footer>
    </article>
  `,
})
export class FullCardComponent {
  private api = inject(ApiService);

  readonly card = input.required<PersonCard>();
  readonly template = input<CardTemplate | null>(null);
  readonly timeline = input<Timeline | null>(null);

  readonly closed = output<void>();
  readonly openRelated = output<number>();
  readonly addPhoto = output<PersonCard>();
  readonly enrich = output<PersonCard>();

  readonly fields = computed(() => resolveFields(this.card(), this.template()));
  readonly initials = computed(() => initials(this.card()));
  readonly crop = computed(() => cropStyle(this.card()));
  readonly photoSrc = computed(() => this.api.mediaUrl(this.card().photo_url));

  photoStyle(): Record<string, string> {
    const size = this.template()?.photo_size ?? 160;
    const shape = this.template()?.photo_shape ?? 'rounded';
    const radius = shape === 'circle' ? '50%' : shape === 'square' ? '2px' : '12px';
    return {
      width: `${size}px`,
      height: `${size}px`,
      'border-radius': radius,
      '--accent': this.card().style.accent_color,
    };
  }
}
