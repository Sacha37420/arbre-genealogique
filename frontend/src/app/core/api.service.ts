import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { KeycloakService } from './keycloak.service';
import {
  CardTemplate,
  EventType,
  Provider,
  ProviderResult,
  SearchPayload,
  Timeline,
  Tree,
  TreeGraph,
  TreeViewSettings,
} from './models';

interface EnvWindow {
  __env?: { apiUrl?: string };
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private kc = inject(KeycloakService);

  private get base(): string {
    return (window as unknown as EnvWindow).__env?.apiUrl ?? 'http://localhost:8090';
  }

  /**
   * URL d'une photo, utilisable telle quelle dans une balise <img>.
   *
   * Le JWT part en paramètre d'URL : une balise <img> ne peut pas porter d'en-tête
   * Authorization, et l'intercepteur ne s'applique qu'aux requêtes HttpClient.
   */
  mediaUrl(path: string | null): string {
    if (!path) return '';
    if (path.startsWith('http')) return path;
    const token = this.kc.getToken();
    return `${this.base}${path}${token ? `?token=${token}` : ''}`;
  }

  // ── Arbres ────────────────────────────────────────────────────────────────
  getTrees(): Observable<Tree[]> {
    return this.list<Tree>(`${this.base}/api/trees/`);
  }

  createTree(payload: Partial<Tree>): Observable<Tree> {
    return this.http.post<Tree>(`${this.base}/api/trees/`, payload);
  }

  getGraph(treeId: number): Observable<TreeGraph> {
    return this.http.get<TreeGraph>(`${this.base}/api/trees/${treeId}/graph/`);
  }

  getSettings(treeId: number): Observable<TreeViewSettings> {
    return this.http.get<TreeViewSettings>(`${this.base}/api/trees/${treeId}/settings/`);
  }

  patchSettings(treeId: number, payload: Partial<TreeViewSettings>): Observable<TreeViewSettings> {
    return this.http.patch<TreeViewSettings>(`${this.base}/api/trees/${treeId}/settings/`, payload);
  }

  /** Enregistre en un seul appel toutes les cartes déplacées à la souris. */
  saveLayout(
    treeId: number,
    positions: { individual?: number; family?: number; x: number; y: number; pinned: boolean }[],
  ): Observable<{ saved: number }> {
    return this.http.post<{ saved: number }>(`${this.base}/api/trees/${treeId}/layout/`, {
      positions,
    });
  }

  importGedcom(treeId: number, file: File): Observable<unknown> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post(`${this.base}/api/trees/${treeId}/import_gedcom/`, form);
  }

  exportGedcom(treeId: number): Observable<Blob> {
    return this.http.get(`${this.base}/api/trees/${treeId}/gedcom/`, { responseType: 'blob' });
  }

  geocodeTree(
    treeId: number,
    credentials: Record<string, string> = {},
  ): Observable<{ geocoded: number; not_found: number }> {
    return this.http.post<{ geocoded: number; not_found: number }>(
      `${this.base}/api/trees/${treeId}/geocode/`,
      { credentials },
    );
  }

  // ── Individus ─────────────────────────────────────────────────────────────
  getTimeline(individualId: number): Observable<Timeline> {
    return this.http.get<Timeline>(`${this.base}/api/individuals/${individualId}/timeline/`);
  }

  createIndividual(payload: {
    tree: number;
    givn?: string;
    surn?: string;
    sex?: string;
  }): Observable<{ id: number }> {
    return this.http.post<{ id: number }>(`${this.base}/api/individuals/`, payload);
  }

  updateIndividual(id: number, payload: Record<string, unknown>): Observable<unknown> {
    return this.http.patch(`${this.base}/api/individuals/${id}/`, payload);
  }

  deleteIndividual(id: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/individuals/${id}/`);
  }

  // ── Familles ──────────────────────────────────────────────────────────────
  createFamily(treeId: number): Observable<{ id: number }> {
    return this.http.post<{ id: number }>(`${this.base}/api/families/`, { tree: treeId });
  }

  addSpouse(familyId: number, individual: number): Observable<unknown> {
    return this.http.post(`${this.base}/api/families/${familyId}/add_spouse/`, { individual });
  }

  addChild(familyId: number, individual: number): Observable<unknown> {
    return this.http.post(`${this.base}/api/families/${familyId}/add_child/`, { individual });
  }

  // ── Événements ────────────────────────────────────────────────────────────
  getEventTypes(): Observable<EventType[]> {
    return this.http.get<EventType[]>(`${this.base}/api/events/types/`);
  }

  createEvent(payload: Record<string, unknown>): Observable<unknown> {
    return this.http.post(`${this.base}/api/events/`, payload);
  }

  updateEvent(id: number, payload: Record<string, unknown>): Observable<unknown> {
    return this.http.patch(`${this.base}/api/events/${id}/`, payload);
  }

  deleteEvent(id: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/events/${id}/`);
  }

  // ── Médias ────────────────────────────────────────────────────────────────
  uploadPhoto(treeId: number, individualId: number, file: File): Observable<unknown> {
    const form = new FormData();
    form.append('file', file);
    form.append('tree', String(treeId));
    form.append('individual', String(individualId));
    return this.http.post(`${this.base}/api/media/`, form);
  }

  // ── Gabarits de cartes ────────────────────────────────────────────────────
  getCardTemplates(treeId: number): Observable<CardTemplate[]> {
    return this.list<CardTemplate>(`${this.base}/api/card-templates/?tree=${treeId}`);
  }

  updateCardTemplate(id: number, payload: Partial<CardTemplate>): Observable<CardTemplate> {
    return this.http.patch<CardTemplate>(`${this.base}/api/card-templates/${id}/`, payload);
  }

  // ── Styles ────────────────────────────────────────────────────────────────
  getNodeStyles(treeId: number): Observable<Record<string, unknown>[]> {
    return this.list(`${this.base}/api/node-styles/?tree=${treeId}`);
  }

  updateNodeStyle(id: number, payload: Record<string, unknown>): Observable<unknown> {
    return this.http.patch(`${this.base}/api/node-styles/${id}/`, payload);
  }

  getStyleRules(treeId: number): Observable<Record<string, unknown>[]> {
    return this.list(`${this.base}/api/style-rules/?tree=${treeId}`);
  }

  updateStyleRule(id: number, payload: Record<string, unknown>): Observable<unknown> {
    return this.http.patch(`${this.base}/api/style-rules/${id}/`, payload);
  }

  getEdgeStyles(treeId: number): Observable<Record<string, unknown>[]> {
    return this.list(`${this.base}/api/edge-styles/?tree=${treeId}`);
  }

  updateEdgeStyle(id: number, payload: Record<string, unknown>): Observable<unknown> {
    return this.http.patch(`${this.base}/api/edge-styles/${id}/`, payload);
  }

  // ── Enrichissement ────────────────────────────────────────────────────────
  getProviders(): Observable<Provider[]> {
    return this.http.get<Provider[]>(`${this.base}/api/enrich/providers/`);
  }

  /** Les clés d'API partent dans le corps de la requête ; le serveur ne les conserve pas. */
  searchProvider(
    payload: SearchPayload,
  ): Observable<{ provider: string; count: number; results: ProviderResult[] }> {
    return this.http.post<{ provider: string; count: number; results: ProviderResult[] }>(
      `${this.base}/api/enrich/search/`,
      payload,
    );
  }

  fetchProvider(
    provider: string,
    externalId: string,
    credentials: Record<string, string> = {},
  ): Observable<ProviderResult> {
    return this.http.post<ProviderResult>(`${this.base}/api/enrich/fetch/`, {
      provider,
      external_id: externalId,
      credentials,
    });
  }

  importFromProvider(payload: {
    provider: string;
    external_id: string;
    tree: number;
    target?: number | null;
    with_relatives?: boolean;
    with_photo?: boolean;
    credentials?: Record<string, string>;
  }): Observable<unknown> {
    return this.http.post(`${this.base}/api/enrich/import/`, payload);
  }

  /** DRF renvoie une liste nue ou un objet paginé selon la configuration. */
  private list<T>(url: string): Observable<T[]> {
    return this.http
      .get<T[] | { results?: T[] }>(url)
      .pipe(map((response) => (Array.isArray(response) ? response : (response.results ?? []))));
  }
}
