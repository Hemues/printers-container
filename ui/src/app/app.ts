import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { FontAwesomeModule } from '@fortawesome/angular-fontawesome';
import { NgbModule } from '@ng-bootstrap/ng-bootstrap';
import {
  faSun, faMoon, faCircleHalfStroke, faUser, faUserShield, faSignOutAlt, faKey,
  faShieldAlt, faEnvelope, faRightFromBracket, faClipboardList, faPrint, faCheck,
} from '@fortawesome/free-solid-svg-icons';

import { AuthService } from './services/auth.service';
import { PrintersSocket } from './services/printers-socket.service';
import { Themes } from './theme';
import { Theme } from './interfaces';
import {
  LoginComponent, AdminPanelComponent, ChangePasswordComponent,
  LogViewerComponent, TwoFactorSetupComponent,
} from './components/';

interface PrintStatsBucket { pages: number; jobs: number; }
interface PrintStats {
  today: PrintStatsBucket;
  month: PrintStatsBucket;
  year: PrintStatsBucket;
  overall: PrintStatsBucket;
}

@Component({
  selector: 'app-root',
  imports: [
    FormsModule, FontAwesomeModule, NgbModule,
    LoginComponent, AdminPanelComponent, ChangePasswordComponent,
    LogViewerComponent, TwoFactorSetupComponent,
  ],
  templateUrl: './app.html',
  styleUrl: './app.sass',
})
export class App implements OnInit {
  auth = inject(AuthService);
  private socket = inject(PrintersSocket);
  private http = inject(HttpClient);

  faSun = faSun; faMoon = faMoon; faCircleHalfStroke = faCircleHalfStroke;
  faUser = faUser; faUserShield = faUserShield; faSignOutAlt = faSignOutAlt;
  faKey = faKey; faShieldAlt = faShieldAlt; faEnvelope = faEnvelope;
  faRightFromBracket = faRightFromBracket; faClipboardList = faClipboardList;
  faPrint = faPrint;
  faCheck = faCheck;

  themes: Theme[] = Themes;
  activeTheme: Theme | undefined;

  // UI panels
  showAdminPanel = false;
  showChangePassword = false;
  showUserMenu = false;
  showLogViewer = false;
  show2faSetup = false;
  twoFactorMode: 'enable' | 'disable' = 'enable';

  toasts: { msg: string; type: 'error' | 'success' | 'info' }[] = [];

  // Stats
  stats = signal<PrintStats | null>(null);
  hasStats = computed(() => !!this.stats());

  printersVersion: string | null = null;

  ngOnInit() {
    this.applyStoredTheme();
    this.fetchVersion();
    // Unregister any stale service workers from previous builds
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistrations().then(regs => {
        regs.forEach(r => r.unregister());
      });
      caches.keys().then(names => names.forEach(n => caches.delete(n)));
    }
    if (this.auth.token()) {
      this.auth.checkSession().subscribe({
        next: () => this.afterSession(),
        error: () => { /* invalid token cleared inside service */ },
      });
    }
  }

  private applyStoredTheme() {
    const stored = localStorage.getItem('printers_theme') || 'auto';
    const theme = this.themes.find(t => t.id === stored) || this.themes[0];
    this.setTheme(theme);
  }

  setTheme(theme: Theme) {
    this.activeTheme = theme;
    document.documentElement.setAttribute('data-bs-theme', theme.id === 'auto'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : theme.id);
    localStorage.setItem('printers_theme', theme.id);
  }

  cycleTheme() {
    const idx = this.themes.findIndex(t => t === this.activeTheme);
    const next = this.themes[(idx + 1) % this.themes.length];
    this.setTheme(next);
  }

  private afterSession() {
    this.connectSocket();
    this.fetchStats();
  }

  onLoggedIn() {
    this.auth.checkSession().subscribe(() => this.afterSession());
  }

  logout() {
    this.auth.logout().subscribe(() => {
      this.socket.disconnect();
      this.showUserMenu = false;
    });
  }

  private connectSocket() {
    this.socket.reconnectWithToken();
  }

  fetchVersion() {
    this.http.get<{ version: string }>('api/version').subscribe({
      next: (r) => { this.printersVersion = r.version; },
      error: () => { /* ignore */ },
    });
  }

  fetchStats() {
    this.http.get<PrintStats>('api/stats').subscribe({
      next: (r) => this.stats.set(r),
      error: () => this.stats.set(null),
    });
  }

  openTwoFactorSetup(mode: 'enable' | 'disable') {
    this.twoFactorMode = mode;
    this.show2faSetup = true;
    this.showUserMenu = false;
  }

  pushToast(msg: string, type: 'error' | 'success' | 'info' = 'info') {
    this.toasts.push({ msg, type });
    setTimeout(() => this.toasts.shift(), 4000);
  }
}
