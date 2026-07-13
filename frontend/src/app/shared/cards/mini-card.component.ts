import { NgStyle } from '@angular/common';
import { Component, computed, inject, input, output } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { CardTemplate, PersonCard } from '../../core/models';
import { cropStyle, initials, resolveFields } from './card-fields';

/**
 * La mini-carte : le nœud de l'arbre.
 *
 * Disposition par défaut — photo à gauche, informations empilées à droite (nom,
 * prénom, naissance, décès si la personne est décédée) — mais tout est piloté par
 * le gabarit : position de la photo, champs affichés, ordre, typographie. La même
 * disposition sert de source unique à l'arbre et à l'aperçu du paramétrage.
 */
@Component({
  selector: 'app-mini-card',
  standalone: true,
  imports: [NgStyle],
  styleUrl: './mini-card.component.scss',
  template: `
    <div
      class="mini"
      [class.selected]="selected()"
      [class.deceased]="!card().is_living"
      [class.photo-top]="photoOnTop()"
      [class.photo-right]="template()?.photo_position === 'RIGHT'"
      [ngStyle]="boxStyle()"
    >
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

      <div class="fields">
        @for (field of fields(); track field.key) {
          <span
            class="field"
            [style.font-weight]="field.bold ? 700 : 400"
            [style.font-size.px]="field.size"
            [style.color]="field.color"
            [style.text-transform]="field.uppercase ? 'uppercase' : 'none'"
            [title]="field.label + ' : ' + field.value"
          >
            {{ field.value }}
          </span>
        }
      </div>

      @if (!card().is_living && template()?.deceased_marker === 'cross') {
        <span class="marker" title="Décédé·e">†</span>
      }
    </div>
  `,
})
export class MiniCardComponent {
  private api = inject(ApiService);

  readonly card = input.required<PersonCard>();
  readonly template = input<CardTemplate | null>(null);
  readonly selected = input(false);

  readonly cardClick = output<PersonCard>();

  readonly fields = computed(() => resolveFields(this.card(), this.template()));
  readonly initials = computed(() => initials(this.card()));
  readonly crop = computed(() => cropStyle(this.card()));
  readonly photoSrc = computed(() => this.api.mediaUrl(this.card().photo_url));

  readonly photoOnTop = computed(() => {
    const position = this.template()?.photo_position;
    return position === 'TOP' || position === 'TOP_LEFT';
  });

  boxStyle(): Record<string, string> {
    const style = this.card().style;
    const shadows: Record<string, string> = {
      none: 'none',
      sm: '0 1px 3px rgba(20, 30, 45, 0.10), 0 1px 2px rgba(20, 30, 45, 0.06)',
      md: '0 4px 12px rgba(20, 30, 45, 0.12)',
      lg: '0 10px 28px rgba(20, 30, 45, 0.18)',
    };
    return {
      width: `${style.width}px`,
      'min-height': `${style.height}px`,
      background: style.background_gradient || style.background_color,
      'border-color': style.border_color,
      'border-width': `${style.border_width}px`,
      'border-style': style.border_style,
      'border-radius': `${style.border_radius}px`,
      color: style.text_color,
      'font-family': style.font_family || 'inherit',
      'font-size': `${style.font_size}px`,
      'font-weight': style.font_weight,
      'box-shadow': shadows[style.shadow] ?? shadows['sm'],
      opacity: String(style.opacity),
      '--accent': style.accent_color,
    };
  }

  photoStyle(): Record<string, string> {
    const size = this.template()?.photo_size ?? this.card().style.photo_size;
    const shape = this.template()?.photo_shape ?? this.card().style.photo_shape;
    const radius = shape === 'circle' ? '50%' : shape === 'square' ? '2px' : '8px';
    return {
      width: `${size}px`,
      height: `${size}px`,
      'border-radius': radius,
    };
  }
}
