import { NgStyle } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ApiService } from '../../core/api.service';
import {
  JUNCTION_SIZE,
  Positioned,
  SnapResult,
  computeLayout,
  edgePath,
  isHorizontal,
  snapPosition,
} from '../../core/layout';
import {
  Edge,
  EdgeStyleSpec,
  FamilyNode,
  PersonCard,
  Timeline,
  TreeGraph,
  TreeShare,
} from '../../core/models';
import { TreeStore, describeError } from '../../core/tree-store';
import { FullCardComponent } from '../../shared/cards/full-card.component';
import { MiniCardComponent } from '../../shared/cards/mini-card.component';
import { PersonEditorComponent } from '../../shared/cards/person-editor.component';

interface RenderedEdge {
  id: string;
  path: string;
  style: EdgeStyleSpec;
  /** Le lien sous-jacent, pour pouvoir le supprimer d'un clic sur le trait. */
  edge: Edge;
}

/** En deçà de ce déplacement (en pixels écran), le geste est un clic, pas un glissement. */
const DRAG_THRESHOLD_PX = 4;

const DEFAULT_EDGE: EdgeStyleSpec = {
  color: '#b6c0cd',
  width: 2,
  dash: 'solid',
  curve: 'orthogonal',
  marker_end: '',
  opacity: 1,
};

@Component({
  selector: 'app-tree',
  standalone: true,
  imports: [NgStyle, FormsModule, MiniCardComponent, FullCardComponent, PersonEditorComponent],
  templateUrl: './tree.component.html',
  styleUrl: './tree.component.scss',
})
export class TreeComponent {
  private api = inject(ApiService);
  private router = inject(Router);
  readonly store = inject(TreeStore);

  readonly graph = signal<TreeGraph | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');

  readonly selected = signal<PersonCard | null>(null);
  readonly timeline = signal<Timeline | null>(null);
  readonly editing = signal(false);

  readonly zoom = signal(1);
  readonly pan = signal<Positioned>({ x: 0, y: 0 });

  /** Positions en cours de glissement, superposées à celles calculées. */
  private readonly moved = signal(new Map<string, Positioned>());

  private drag: {
    key: string;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    moved: boolean;
  } | null = null;
  private panning: { startX: number; startY: number; originX: number; originY: number } | null = null;

  /** Un vrai glissement se termine par un « click » : il ne doit pas ouvrir la fiche. */
  private suppressClick = false;

  /** Ligne d'aimantation visée pendant le glissement (trait d'aide). */
  readonly guide = signal<{ horizontal: boolean; at: number } | null>(null);

  readonly newPersonName = signal('');
  readonly busy = signal('');

  // ── Partage ───────────────────────────────────────────────────────────────
  /**
   * Rôle de l'utilisateur sur l'arbre courant, calculé par le serveur.
   *
   * L'interface s'en sert pour ne montrer que ce qui est permis. Ce n'est
   * qu'un confort : l'autorisation elle-même est tenue côté serveur, où toutes
   * les écritures repassent par `check_tree`.
   */
  readonly canEdit = computed(() => this.store.current()?.my_role !== 'VIEWER');
  readonly isOwner = computed(() => this.store.current()?.my_role === 'OWNER');

  readonly sharing = signal(false);
  readonly shares = signal<TreeShare[]>([]);
  readonly shareEmail = signal('');
  readonly shareRole = signal<TreeShare['role']>('VIEWER');

  constructor() {
    this.store.load();

    // Recharger le graphe dès que l'arbre courant change.
    effect(() => {
      const treeId = this.store.currentId();
      if (treeId === null) {
        this.graph.set(null);
        return;
      }
      this.reload(treeId);
    });
  }

  // ── Chargement ────────────────────────────────────────────────────────────
  reload(treeId = this.store.currentId()): void {
    if (treeId === null) return;
    this.loading.set(true);
    this.error.set('');

    this.api.getGraph(treeId).subscribe({
      next: (graph) => {
        this.graph.set(graph);
        this.moved.set(new Map());
        this.zoom.set(graph.settings.zoom || 1);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(describeError(err));
        this.loading.set(false);
      },
    });
  }

  // ── Mise en page ──────────────────────────────────────────────────────────
  private readonly layout = computed(() => {
    const graph = this.graph();
    if (!graph) return null;
    return computeLayout(graph.nodes, graph.families, graph.edges, graph.settings);
  });

  readonly canvasSize = computed(() => {
    const layout = this.layout();
    return { width: (layout?.width ?? 800) + 200, height: (layout?.height ?? 600) + 200 };
  });

  positionOf(key: string): Positioned {
    const override = this.moved().get(key);
    if (override) return override;

    const layout = this.layout();
    if (!layout) return { x: 0, y: 0 };

    const id = Number(key.slice(1));
    const found = key.startsWith('i') ? layout.individuals.get(id) : layout.families.get(id);
    return found ?? { x: 0, y: 0 };
  }

  /** Coin haut-gauche de la carte : dagre renvoie un centre, le DOM veut un coin. */
  cardOrigin(node: PersonCard): Positioned {
    const center = this.positionOf(`i${node.id}`);
    return {
      x: center.x - (node.style?.width ?? 220) / 2,
      y: center.y - (node.style?.height ?? 86) / 2,
    };
  }

  junctionOrigin(family: FamilyNode): Positioned {
    const center = this.positionOf(`f${family.id}`);
    return { x: center.x - JUNCTION_SIZE / 2, y: center.y - JUNCTION_SIZE / 2 };
  }

  readonly edges = computed<RenderedEdge[]>(() => {
    const graph = this.graph();
    if (!graph) return [];
    // Dépendance explicite : un glissement doit redessiner les liens.
    this.moved();

    const orientation = graph.settings.orientation || 'TB';

    return graph.edges.map((edge: Edge) => {
      const style = graph.edge_styles[edge.kind] ?? DEFAULT_EDGE;
      const from = this.positionOf(edge.source);
      const to = this.positionOf(edge.target);
      return {
        id: `${edge.source}->${edge.target}`,
        path: edgePath(from, to, style.curve, orientation),
        style,
        edge,
      };
    });
  });

  /** Nom de la personne au bout d'un lien — l'autre extrémité est la famille. */
  private personOn(edge: Edge): string {
    const key = edge.kind === 'SPOUSE' ? edge.source : edge.target;
    const id = Number(key.slice(1));
    const card = this.graph()?.nodes.find((n) => n.id === id);
    return card?.full_name || 'cette personne';
  }

  /**
   * Supprime un lien d'un clic sur le trait qui le porte.
   *
   * Le lien est le seul objet supprimé : les deux personnes restent dans l'arbre.
   * Le serveur se charge d'effacer la famille que le lien laisse éventuellement
   * vide, pour ne pas abandonner un nœud de jonction relié à personne.
   */
  removeEdge(rendered: RenderedEdge): void {
    if (!this.canEdit()) return;

    const edge = rendered.edge;
    const who = this.personOn(edge);

    const question =
      edge.kind === 'SPOUSE'
        ? `Retirer ${who} de cette union ?`
        : `Détacher ${who} de ses parents ?`;
    if (!confirm(`${question}\n\nSeul le lien est supprimé : la personne reste dans l’arbre.`)) return;

    const call =
      edge.kind === 'SPOUSE'
        ? this.api.removeSpouseLink(edge.link)
        : this.api.removeChildLink(edge.link);

    this.busy.set('Suppression du lien…');
    call.subscribe({
      next: () => {
        this.busy.set('');
        this.refreshGraph();
      },
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  /** La personne supprimée : sa fiche n'a plus d'objet, l'arbre doit être relu. */
  onRemoved(): void {
    this.closeCard();
    this.reload();
  }

  dashArray(style: EdgeStyleSpec): string {
    if (style.dash === 'dashed') return '7 5';
    if (style.dash === 'dotted') return '2 4';
    return '';
  }

  // ── Zoom & déplacement ────────────────────────────────────────────────────
  onWheel(event: WheelEvent): void {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    const next = Math.min(Math.max(this.zoom() * factor, 0.2), 3);

    // Zoom centré sur le curseur : sans cette correction, le contenu fuit sous la souris.
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const mouseX = event.clientX - rect.left;
    const mouseY = event.clientY - rect.top;
    const pan = this.pan();
    const scale = next / this.zoom();

    this.pan.set({
      x: mouseX - (mouseX - pan.x) * scale,
      y: mouseY - (mouseY - pan.y) * scale,
    });
    this.zoom.set(next);
  }

  onCanvasPointerDown(event: PointerEvent): void {
    if (event.button !== 0) return;
    const pan = this.pan();
    this.panning = { startX: event.clientX, startY: event.clientY, originX: pan.x, originY: pan.y };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }

  onNodePointerDown(event: PointerEvent, key: string): void {
    if (event.button !== 0) return;
    // Déplacer une carte, c'est écrire (la position est épinglée en base) : sur un
    // arbre partagé en lecture, la carte ne bouge pas. Le clic, lui, ouvre la fiche.
    if (!this.canEdit()) return;
    event.stopPropagation();

    const origin = this.positionOf(key);
    this.drag = {
      key,
      startX: event.clientX,
      startY: event.clientY,
      originX: origin.x,
      originY: origin.y,
      moved: false,
    };
    (event.target as HTMLElement).setPointerCapture(event.pointerId);
  }

  onPointerMove(event: PointerEvent): void {
    const scale = this.zoom();

    if (this.drag) {
      const dx = (event.clientX - this.drag.startX) / scale;
      const dy = (event.clientY - this.drag.startY) / scale;

      // Un clic n'est jamais parfaitement immobile : en deçà du seuil, c'est un clic,
      // pas un déplacement — sinon ouvrir une fiche épinglerait la carte au passage.
      if (!this.drag.moved && Math.hypot(dx, dy) * scale < DRAG_THRESHOLD_PX) return;
      this.drag.moved = true;

      const free = { x: this.drag.originX + dx, y: this.drag.originY + dy };

      // Maj : aimantation désactivée d'un bout à l'autre du geste.
      const snapped = event.shiftKey
        ? { position: free, row: null }
        : this.snap(free);

      const next = new Map(this.moved());
      next.set(this.drag.key, snapped.position);
      this.moved.set(next);

      // Le trait d'aide n'apparaît que si la carte est effectivement attirée :
      // loin de toute ligne, elle suit librement la souris.
      this.guide.set(snapped.row === null ? null : this.guideFor(snapped.row));
      return;
    }

    if (this.panning) {
      this.pan.set({
        x: this.panning.originX + (event.clientX - this.panning.startX),
        y: this.panning.originY + (event.clientY - this.panning.startY),
      });
    }
  }

  /**
   * Aimante une position — seulement si elle passe assez près d'une ligne.
   *
   * Au-delà du rayon d'attraction, la carte reste où la souris la mène : c'est ce
   * qui permet de la sortir des rangées existantes et d'en ouvrir une nouvelle.
   */
  private snap(position: Positioned): SnapResult {
    const layout = this.layout();
    const settings = this.graph()?.settings;
    if (!layout || !settings) return { position, row: null };

    return snapPosition(position, {
      rows: layout.rows,
      orientation: settings.orientation || 'TB',
      gridSize: settings.grid_size || 16,
      snapToGrid: settings.snap_to_grid,
    });
  }

  /** Trait d'aide montrant la ligne sur laquelle la carte va se poser. */
  private guideFor(row: number): { horizontal: boolean; at: number } | null {
    const settings = this.graph()?.settings;
    if (!settings) return null;

    const horizontal = isHorizontal(settings.orientation || 'TB');
    return { horizontal: !horizontal, at: row };
  }

  onPointerUp(): void {
    this.guide.set(null);

    if (this.drag) {
      const { key, moved } = this.drag;
      this.drag = null;
      this.suppressClick = moved;

      if (moved) {
        const treeId = this.store.currentId();
        const position = this.positionOf(key);

        // La carte déplacée est épinglée : le prochain calcul automatique la laissera là.
        if (treeId !== null) {
          const id = Number(key.slice(1));
          const target = key.startsWith('i') ? { individual: id } : { family: id };
          this.api
            .saveLayout(treeId, [{ ...target, x: position.x, y: position.y, pinned: true }])
            .subscribe({ error: (err) => this.error.set(describeError(err)) });
        }
      }
    }
    this.panning = null;
  }

  /** Rend la main au moteur de placement : toutes les cartes sont désépinglées. */
  autoLayout(): void {
    const graph = this.graph();
    const treeId = this.store.currentId();
    if (!graph || treeId === null) return;

    const positions = [
      ...graph.nodes.map((n) => ({ individual: n.id, x: 0, y: 0, pinned: false })),
      ...graph.families.map((f) => ({ family: f.id, x: 0, y: 0, pinned: false })),
    ];
    if (!positions.length) return;

    this.busy.set('Réorganisation…');
    this.api.saveLayout(treeId, positions).subscribe({
      next: () => {
        this.busy.set('');
        this.reload(treeId);
      },
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  resetView(): void {
    this.zoom.set(1);
    this.pan.set({ x: 0, y: 0 });
  }

  /**
   * Bascule un réglage de vue (aimantation, grille) et le conserve.
   *
   * Ces réglages existaient déjà en base et dans l'API, mais rien ne les lisait :
   * ils sont désormais branchés sur l'arbre et persistés d'une session à l'autre.
   */
  toggleSetting(key: 'snap_to_grid' | 'show_grid'): void {
    const graph = this.graph();
    const treeId = this.store.currentId();
    if (!graph || treeId === null) return;

    const value = !graph.settings[key];
    this.graph.set({ ...graph, settings: { ...graph.settings, [key]: value } });

    this.api.patchSettings(treeId, { [key]: value }).subscribe({
      error: (err) => this.error.set(describeError(err)),
    });
  }

  /** Motif de la grille, à l'échelle du canevas (il suit le zoom avec le contenu). */
  gridStyle(): Record<string, string> {
    const size = this.graph()?.settings.grid_size || 16;
    return {
      'background-image':
        'linear-gradient(to right, rgba(20,30,45,0.06) 1px, transparent 1px),' +
        'linear-gradient(to bottom, rgba(20,30,45,0.06) 1px, transparent 1px)',
      'background-size': `${size}px ${size}px`,
    };
  }

  // ── Sélection ─────────────────────────────────────────────────────────────
  select(card: PersonCard): void {
    // Le « click » qui clôt un glissement ne doit pas ouvrir la fiche.
    if (this.suppressClick) {
      this.suppressClick = false;
      return;
    }

    this.selected.set(card);
    this.timeline.set(null);
    this.api.getTimeline(card.id).subscribe({
      next: (timeline) => this.timeline.set(timeline),
      error: (err) => this.error.set(describeError(err)),
    });
  }

  openRelated(individualId: number): void {
    const card = this.graph()?.nodes.find((n) => n.id === individualId);
    if (card) this.select(card);
  }

  closeCard(): void {
    this.selected.set(null);
    this.timeline.set(null);
    this.editing.set(false);
  }

  /**
   * Après une modification, le graphe entier est rechargé : une date de décès
   * ajoutée change le style de la carte (règle « décédé »), un nom change le
   * texte, une naissance change la génération. Recharger la seule fiche laisserait
   * l'arbre en désaccord avec elle.
   */
  onEdited(): void {
    this.editing.set(false);
    this.refreshGraph();
  }

  /**
   * Redessine l'arbre en gardant la fiche ouverte.
   *
   * Une modification déborde toujours de la fiche : une date de décès change le
   * style de la carte (règle « décédé »), un nouveau parent ajoute une carte et
   * décale les générations. Recharger la seule fiche laisserait l'arbre en
   * désaccord avec elle.
   */
  refreshGraph(): void {
    const treeId = this.store.currentId();
    const id = this.selected()?.id;
    if (treeId === null) return;

    this.api.getGraph(treeId).subscribe({
      next: (graph) => {
        this.graph.set(graph);
        const refreshed = graph.nodes.find((n) => n.id === id) ?? null;
        this.selected.set(refreshed);
        if (refreshed) {
          this.api.getTimeline(refreshed.id).subscribe({
            next: (timeline) => this.timeline.set(timeline),
            error: () => this.timeline.set(null),
          });
        }
      },
      error: (err) => this.error.set(describeError(err)),
    });
  }

  // ── Actions ───────────────────────────────────────────────────────────────
  addPerson(): void {
    const treeId = this.store.currentId();
    const name = this.newPersonName().trim();
    if (treeId === null || !name) return;

    const parts = name.split(/\s+/);
    const surn = parts.length > 1 ? parts.pop()! : '';
    const givn = parts.join(' ');

    this.busy.set('Ajout…');
    this.api.createIndividual({ tree: treeId, givn, surn }).subscribe({
      next: () => {
        this.newPersonName.set('');
        this.busy.set('');
        this.reload(treeId);
      },
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  uploadPhoto(card: PersonCard, event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    const treeId = this.store.currentId();
    if (!file || treeId === null) return;

    this.busy.set('Envoi de la photo…');
    this.api.uploadPhoto(treeId, card.id, file).subscribe({
      next: () => {
        this.busy.set('');
        input.value = '';
        this.reload(treeId);
        this.closeCard();
      },
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  importGedcom(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    const treeId = this.store.currentId();
    if (!file || treeId === null) return;

    this.busy.set('Import GEDCOM…');
    this.api.importGedcom(treeId, file).subscribe({
      next: () => {
        this.busy.set('');
        input.value = '';
        this.reload(treeId);
      },
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  exportGedcom(): void {
    const tree = this.store.current();
    if (!tree) return;

    this.api.exportGedcom(tree.id).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${tree.name.replace(/\s+/g, '_')}.ged`;
        link.click();
        URL.revokeObjectURL(url);
      },
      error: (err) => this.error.set(describeError(err)),
    });
  }

  createTree(): void {
    const name = prompt('Nom du nouvel arbre :');
    if (name?.trim()) this.store.create(name.trim());
  }

  // ── Partage ───────────────────────────────────────────────────────────────
  toggleSharing(): void {
    const open = !this.sharing();
    this.sharing.set(open);
    if (open) this.loadShares();
  }

  private loadShares(): void {
    const treeId = this.store.currentId();
    if (treeId === null) return;

    this.api.getShares(treeId).subscribe({
      next: (shares) => this.shares.set(shares),
      error: (err) => this.error.set(describeError(err)),
    });
  }

  /**
   * Invite quelqu'un par son adresse e-mail.
   *
   * L'invité n'a pas besoin d'exister : c'est l'adresse qui est enregistrée, et
   * l'arbre apparaîtra chez lui dès sa première connexion avec cette adresse.
   */
  invite(): void {
    const treeId = this.store.currentId();
    const email = this.shareEmail().trim();
    if (treeId === null || !email) return;

    this.busy.set('Invitation…');
    this.error.set('');

    this.api.addShare(treeId, email, this.shareRole()).subscribe({
      next: () => {
        this.shareEmail.set('');
        this.busy.set('');
        this.loadShares();
        this.store.load(); // le compteur de partages de l'arbre a changé
      },
      error: (err) => {
        this.busy.set('');
        this.error.set(describeError(err));
      },
    });
  }

  changeRole(share: TreeShare, role: TreeShare['role']): void {
    this.api.updateShare(share.id, role).subscribe({
      next: () => this.loadShares(),
      error: (err) => this.error.set(describeError(err)),
    });
  }

  revoke(share: TreeShare): void {
    if (!confirm(`Retirer l’accès de ${share.email} à cet arbre ?`)) return;

    this.api.removeShare(share.id).subscribe({
      next: () => {
        this.loadShares();
        this.store.load();
      },
      error: (err) => this.error.set(describeError(err)),
    });
  }

  goEnrich(card?: PersonCard): void {
    this.router.navigate(['/recherche'], {
      queryParams: card ? { individual: card.id, surname: card.surname, given: card.given_name } : {},
    });
  }
}
