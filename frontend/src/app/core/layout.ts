import * as dagre from '@dagrejs/dagre';

import { Edge, FamilyNode, PersonCard, TreeViewSettings } from './models';

/** Taille du nœud de jonction d'une famille : un petit losange entre les conjoints. */
export const JUNCTION_SIZE = 16;

export interface Positioned {
  x: number;
  y: number;
}

export interface LayoutResult {
  individuals: Map<number, Positioned>;
  families: Map<number, Positioned>;
  /**
   * Coordonnées des lignes où dagre a posé les cartes (leur y en orientation
   * verticale, leur x en orientation horizontale).
   *
   * C'est sur elles que s'aimante un déplacement : on colle la carte à la ligne
   * où se trouvent déjà les autres, plutôt qu'à une ligne théorique — ainsi
   * l'aimantation aligne sur ce que l'utilisateur voit à l'écran.
   */
  rows: number[];
  width: number;
  height: number;
}

/**
 * Place les cartes automatiquement.
 *
 * Un arbre généalogique n'est pas un arbre au sens informatique : un enfant a deux
 * parents, donc plusieurs chemins mènent à lui. C'est un graphe orienté acyclique,
 * que dagre sait mettre en couches — d'où le nœud de jonction par famille, qui
 * ramène « deux parents → un enfant » à des liens simples et non croisés.
 *
 * Les cartes épinglées (déplacées à la main) sont exclues du calcul : on les
 * repose ensuite à leur position exacte. Sans cela, chaque recalcul balaierait
 * le travail de mise en page de l'utilisateur.
 */
export function computeLayout(
  nodes: PersonCard[],
  families: FamilyNode[],
  edges: Edge[],
  settings: TreeViewSettings,
): LayoutResult {
  const graph = new dagre.graphlib.Graph({ compound: false, multigraph: true });

  graph.setGraph({
    rankdir: settings.orientation || 'TB',
    nodesep: settings.node_spacing_x || 40,
    ranksep: settings.generation_spacing || 120,
    marginx: 60,
    marginy: 60,
    // Le placement « network-simplex » minimise les croisements : c'est ce qui
    // rend une fratrie lisible sur une même ligne.
    ranker: 'network-simplex',
  });
  graph.setDefaultEdgeLabel(() => ({}));

  const visible = nodes.filter((n) => !n.hidden);
  for (const node of visible) {
    graph.setNode(`i${node.id}`, {
      width: node.style?.width ?? 220,
      height: node.style?.height ?? 86,
    });
  }
  for (const family of families) {
    graph.setNode(`f${family.id}`, { width: JUNCTION_SIZE, height: JUNCTION_SIZE });
  }

  const known = new Set(graph.nodes());
  for (const edge of edges) {
    if (known.has(edge.source) && known.has(edge.target)) {
      graph.setEdge(edge.source, edge.target, {}, `${edge.source}->${edge.target}`);
    }
  }

  dagre.layout(graph);

  const individuals = new Map<number, Positioned>();
  const familyPositions = new Map<number, Positioned>();

  for (const node of visible) {
    const computed = graph.node(`i${node.id}`);
    individuals.set(
      node.id,
      node.pinned ? { x: node.x, y: node.y } : { x: computed?.x ?? 0, y: computed?.y ?? 0 },
    );
  }
  for (const family of families) {
    const computed = graph.node(`f${family.id}`);
    familyPositions.set(
      family.id,
      family.pinned ? { x: family.x, y: family.y } : { x: computed?.x ?? 0, y: computed?.y ?? 0 },
    );
  }

  const meta = graph.graph();
  return {
    individuals,
    families: familyPositions,
    rows: collectRows(graph, visible, settings.orientation || 'TB'),
    width: meta.width ?? 1000,
    height: meta.height ?? 800,
  };
}

/**
 * Distance (en unités du canevas) en deçà de laquelle une carte est attirée par
 * une ligne.
 *
 * L'aimantation est une assistance, pas une contrainte : au-delà de ce rayon on
 * place librement, ce qui permet de tirer une carte hors des rangées existantes
 * pour en ouvrir une nouvelle.
 */
export const SNAP_RADIUS = 26;

/** Deux lignes plus proches que cela sont la même : évite d'en empiler des quasi-jumelles. */
const ROW_MERGE_TOLERANCE = 10;

/** Est-on en orientation horizontale (les générations sont alors des colonnes) ? */
export function isHorizontal(orientation: string): boolean {
  return orientation === 'LR' || orientation === 'RL';
}

/**
 * Lignes sur lesquelles une carte peut s'aimanter.
 *
 * Ce sont celles calculées par dagre **et** celles créées à la main : une carte
 * épinglée hors des rangées ouvre une nouvelle ligne, à laquelle les suivantes
 * pourront s'aligner. C'est le seul moyen de composer ses propres rangées.
 */
function collectRows(
  graph: dagre.graphlib.Graph,
  nodes: PersonCard[],
  orientation: string,
): number[] {
  const axis = isHorizontal(orientation) ? 'x' : 'y';
  const rows: number[] = [];

  const add = (value: number): void => {
    if (!rows.some((row) => Math.abs(row - value) < ROW_MERGE_TOLERANCE)) rows.push(value);
  };

  for (const node of nodes) {
    if (node.pinned) {
      add(node[axis]);
      continue;
    }
    const computed = graph.node(`i${node.id}`) as Positioned | undefined;
    if (computed) add(computed[axis]);
  }

  return rows.sort((a, b) => a - b);
}

export interface SnapOptions {
  rows: number[];
  orientation: string;
  gridSize: number;
  snapToGrid: boolean;
  /** Rayon d'attraction ; au-delà, la position reste libre. */
  radius?: number;
}

export interface SnapResult {
  position: Positioned;
  /** Ligne sur laquelle la carte s'est posée, ou null si elle est restée libre. */
  row: number | null;
}

/**
 * Aimante une position en cours de glissement — si elle est assez proche d'une ligne.
 *
 * Loin de toute ligne, rien n'est corrigé sur l'axe des générations : la carte
 * suit la souris, et l'endroit où on la lâche devient une nouvelle rangée.
 */
export function snapPosition(position: Positioned, options: SnapOptions): SnapResult {
  const horizontal = isHorizontal(options.orientation);
  const generationAxis = horizontal ? 'x' : 'y';
  const freeAxis = horizontal ? 'y' : 'x';

  const snapped: Positioned = { ...position };

  const row = nearestRow(
    position[generationAxis],
    options.rows,
    options.radius ?? SNAP_RADIUS,
  );
  if (row !== null) snapped[generationAxis] = row;

  if (options.snapToGrid && options.gridSize > 0) {
    snapped[freeAxis] = Math.round(position[freeAxis] / options.gridSize) * options.gridSize;
  }

  return { position: snapped, row };
}

/** Ligne la plus proche dans le rayon donné, ou null s'il n'y en a aucune assez près. */
export function nearestRow(value: number, rows: number[], radius = SNAP_RADIUS): number | null {
  let best: number | null = null;
  let bestDistance = radius;

  for (const row of rows) {
    const distance = Math.abs(row - value);
    if (distance <= bestDistance) {
      best = row;
      bestDistance = distance;
    }
  }

  return best;
}

/**
 * Chemin SVG d'un lien.
 *
 * Les liens de filiation descendent en équerre (le tracé classique d'un arbre
 * généalogique), les unions se rejoignent en ligne droite.
 */
export function edgePath(
  from: Positioned,
  to: Positioned,
  curve: string,
  orientation: string,
): string {
  if (curve === 'straight') {
    return `M ${from.x} ${from.y} L ${to.x} ${to.y}`;
  }

  const horizontal = orientation === 'LR' || orientation === 'RL';

  if (curve === 'bezier') {
    return horizontal
      ? `M ${from.x} ${from.y} C ${(from.x + to.x) / 2} ${from.y}, ${(from.x + to.x) / 2} ${to.y}, ${to.x} ${to.y}`
      : `M ${from.x} ${from.y} C ${from.x} ${(from.y + to.y) / 2}, ${to.x} ${(from.y + to.y) / 2}, ${to.x} ${to.y}`;
  }

  // Équerre : on descend jusqu'à mi-chemin, on se décale, puis on rejoint la cible.
  const radius = 10;
  if (horizontal) {
    if (Math.abs(to.y - from.y) < 2) return `M ${from.x} ${from.y} L ${to.x} ${to.y}`;
    const midX = (from.x + to.x) / 2;
    const dir = to.y > from.y ? radius : -radius;
    return [
      `M ${from.x} ${from.y}`,
      `L ${midX - radius} ${from.y}`,
      `Q ${midX} ${from.y} ${midX} ${from.y + dir}`,
      `L ${midX} ${to.y - dir}`,
      `Q ${midX} ${to.y} ${midX + radius} ${to.y}`,
      `L ${to.x} ${to.y}`,
    ].join(' ');
  }

  const midY = (from.y + to.y) / 2;
  if (Math.abs(to.x - from.x) < 2) return `M ${from.x} ${from.y} L ${to.x} ${to.y}`;
  const dir = to.x > from.x ? radius : -radius;
  return [
    `M ${from.x} ${from.y}`,
    `L ${from.x} ${midY - radius}`,
    `Q ${from.x} ${midY} ${from.x + dir} ${midY}`,
    `L ${to.x - dir} ${midY}`,
    `Q ${to.x} ${midY} ${to.x} ${midY + radius}`,
    `L ${to.x} ${to.y}`,
  ].join(' ');
}
