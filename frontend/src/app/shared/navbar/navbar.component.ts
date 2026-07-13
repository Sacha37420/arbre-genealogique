import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { KeycloakService } from '../../core/keycloak.service';

@Component({
  selector: 'app-navbar',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  styleUrl: './navbar.component.scss',
  template: `
    <nav class="navbar">
      <a class="brand" routerLink="/">
        <span class="mark" aria-hidden="true">🌳</span>
        Arbre généalogique
      </a>

      <div class="nav-links">
        <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Arbre</a>
        <a routerLink="/parametrage" routerLinkActive="active">Paramétrage</a>
        <a routerLink="/recherche" routerLinkActive="active">Recherche</a>
      </div>

      <div class="nav-user">
        <span class="username">{{ username }}</span>
        <button type="button" class="btn-logout" (click)="logout()">Déconnexion</button>
      </div>
    </nav>
  `,
})
export class NavbarComponent {
  private kc = inject(KeycloakService);

  get username(): string {
    return this.kc.username || this.kc.email;
  }

  logout(): void {
    this.kc.logout();
  }
}
