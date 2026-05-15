import { inject, Injectable, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap, catchError, of } from 'rxjs';

export interface LoginResponse {
  status: string;
  token?: string;
  username?: string;
  group?: string;
  must_change_password?: boolean;
  first_run?: boolean;
  msg?: string;
  '2fa_token'?: string;
  '2fa_penalty'?: number;
}

export interface MeResponse {
  status: string;
  username: string;
  group: string;
  must_change_password: boolean;
  has_cookies: boolean;
  totp_enabled: boolean;
  email: string;
  email_status: string;
  smtp_configured: boolean;
  first_run: boolean;
  locked_settings?: string[];
  storage_quota?: number;
  storage_used?: number;
  storage_reserved?: number;
  storage_quota_str?: string;
}

export interface UserRecord {
  username: string;
  group: string;
  enabled: boolean;
  must_change_password: boolean;
  homedir: string;
  locked_settings: string[];
  has_cookies: boolean;
  totp_enabled: boolean;
  email: string;
  email_status: string;
}

export interface SmtpConfig {
  host: string;
  port: number;
  username: string;
  has_password: boolean;
  security: string;
  sender_name: string;
  from_address: string;
  status: string;
}

export interface TwoFactorSetupResponse {
  status: string;
  secret: string;
  qr_code: string;
  uri: string;
}

export interface TwoFactorVerifyResponse {
  status: string;
  token?: string;
  username?: string;
  group?: string;
  must_change_password?: boolean;
  first_run?: boolean;
  msg?: string;
  '2fa_retries_left'?: number;
  '2fa_penalty'?: number;
  '2fa_failed'?: boolean;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private http = inject(HttpClient);

  private _token = signal<string | null>(localStorage.getItem('Printers_token'));
  private _username = signal<string | null>(localStorage.getItem('Printers_username'));
  private _group = signal<string | null>(localStorage.getItem('Printers_group'));
  private _mustChangePassword = signal(false);
  private _firstRun = signal(false);
  private _hasCookies = signal(false);
  private _totpEnabled = signal(false);
  private _email = signal('');
  private _emailStatus = signal('none');
  private _smtpConfigured = signal(false);
  private _lockedSettings = signal<string[]>([]);
  private _storageQuota = signal(0);
  private _storageUsed = signal(0);
  private _storageReserved = signal(0);
  private _storageQuotaStr = signal('0');

  readonly token = this._token.asReadonly();
  readonly username = this._username.asReadonly();
  readonly group = this._group.asReadonly();
  readonly mustChangePassword = this._mustChangePassword.asReadonly();
  readonly firstRun = this._firstRun.asReadonly();
  readonly hasCookies = this._hasCookies.asReadonly();
  readonly totpEnabled = this._totpEnabled.asReadonly();
  readonly email = this._email.asReadonly();
  readonly emailStatus = this._emailStatus.asReadonly();
  readonly smtpConfigured = this._smtpConfigured.asReadonly();
  readonly lockedSettings = this._lockedSettings.asReadonly();
  readonly storageQuota = this._storageQuota.asReadonly();
  readonly storageUsed = this._storageUsed.asReadonly();
  readonly storageReserved = this._storageReserved.asReadonly();
  readonly storageQuotaStr = this._storageQuotaStr.asReadonly();
  readonly storagePercent = computed(() => {
    const q = this._storageQuota();
    if (q <= 0) return 0;
    return Math.min(100, Math.round((this._storageUsed() + this._storageReserved()) / q * 100));
  });
  readonly isOverQuota = computed(() => {
    const q = this._storageQuota();
    if (q <= 0) return false;
    return (this._storageUsed() + this._storageReserved()) >= q;
  });
  readonly isNearQuota = computed(() => {
    const q = this._storageQuota();
    if (q <= 0) return false;
    return this.storagePercent() >= 95 && !this.isOverQuota();
  });
  readonly isAdmin = computed(() => this._group() === 'admin-group' || this._group() === 'useradmin-group');
  readonly isAdminGroup = computed(() => this._group() === 'admin-group');
  readonly isLoggedIn = computed(() => !!this._token());

  login(username: string, password: string): Observable<LoginResponse> {
    return this.http.post<LoginResponse>('api/login', { username, password }).pipe(
      tap((res: LoginResponse) => {
        if (res.status === 'ok' && res.token) {
          this._token.set(res.token);
          this._username.set(res.username!);
          this._group.set(res.group!);
          this._mustChangePassword.set(res.must_change_password ?? false);
          this._firstRun.set(res.first_run ?? false);
          localStorage.setItem('Printers_token', res.token);
          localStorage.setItem('Printers_username', res.username!);
          localStorage.setItem('Printers_group', res.group!);
        }
      })
    );
  }

  logout(): Observable<{ status: string }> {
    return this.http.post<{ status: string }>('api/logout', {}).pipe(
      tap(() => this._clearSession()),
      catchError(() => {
        this._clearSession();
        return of({ status: 'ok' });
      })
    );
  }

  logoutAll(): Observable<{ status: string; sessions_destroyed: number }> {
    return this.http.post<{ status: string; sessions_destroyed: number }>('api/logout-all', {}).pipe(
      tap(() => this._clearSession()),
      catchError(() => {
        this._clearSession();
        return of({ status: 'ok', sessions_destroyed: 0 });
      })
    );
  }

  checkSession(): Observable<MeResponse | null> {
    if (!this._token()) return of(null);
    return this.http.get<MeResponse>('api/me').pipe(
      tap((res: MeResponse) => {
        if (res.status === 'ok') {
          this._username.set(res.username);
          this._group.set(res.group);
          this._mustChangePassword.set(res.must_change_password);
          this._hasCookies.set(res.has_cookies);
          this._totpEnabled.set(res.totp_enabled ?? false);
          this._email.set(res.email ?? '');
          this._emailStatus.set(res.email_status ?? 'none');
          this._smtpConfigured.set(res.smtp_configured ?? false);
          this._firstRun.set(res.first_run);
          this._lockedSettings.set(res.locked_settings || []);
          this._storageQuota.set(res.storage_quota ?? 0);
          this._storageUsed.set(res.storage_used ?? 0);
          this._storageReserved.set(res.storage_reserved ?? 0);
          this._storageQuotaStr.set(res.storage_quota_str ?? '0');
          localStorage.setItem('Printers_username', res.username);
          localStorage.setItem('Printers_group', res.group);
        }
      }),
      catchError(() => {
        this._clearSession();
        return of(null);
      })
    );
  }

  changePassword(currentPassword: string, newPassword: string, newPasswordConfirm: string): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>('api/change-password', {
      current_password: currentPassword,
      new_password: newPassword,
      new_password_confirm: newPasswordConfirm,
    });
  }

  uploadCookies(file: File): Observable<{ status: string }> {
    const formData = new FormData();
    formData.append('cookies', file);
    return this.http.post<{ status: string }>('api/cookies/upload', formData).pipe(
      tap(() => this._hasCookies.set(true))
    );
  }

  removeCookies(): Observable<{ status: string; removed: boolean }> {
    return this.http.post<{ status: string; removed: boolean }>('api/cookies/remove', {}).pipe(
      tap(() => this._hasCookies.set(false))
    );
  }

  refreshStorage(): Observable<{ storage_quota: number; storage_used: number; storage_reserved: number } | null> {
    return this.http.get<{ status: string; storage_quota: number; storage_used: number; storage_reserved: number }>('api/storage').pipe(
      tap((res: { status: string; storage_quota: number; storage_used: number; storage_reserved: number }) => {
        if (res.status === 'ok') {
          this._storageQuota.set(res.storage_quota);
          this._storageUsed.set(res.storage_used);
          this._storageReserved.set(res.storage_reserved);
        }
      }),
      catchError(() => of(null))
    );
  }

  // Admin API
  listUsers(): Observable<{ status: string; users: UserRecord[] }> {
    return this.http.get<{ status: string; users: UserRecord[] }>('api/admin/users');
  }

  createUser(username: string, password: string, group: string): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>('api/admin/users', { username, password, group });
  }

  modifyUser(username: string, changes: Partial<UserRecord>): Observable<{ status: string; msg: string }> {
    return this.http.put<{ status: string; msg: string }>(`api/admin/users/${encodeURIComponent(username)}`, changes);
  }

  deleteUser(username: string): Observable<{ status: string; msg: string }> {
    return this.http.delete<{ status: string; msg: string }>(`api/admin/users/${encodeURIComponent(username)}`);
  }

  resetUserPassword(username: string, newPassword: string): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>(`api/admin/users/${encodeURIComponent(username)}/reset-password`, { password: newPassword });
  }

  adminDisable2fa(username: string): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>(`api/admin/users/${encodeURIComponent(username)}/disable-2fa`, {});
  }

  // --- Email ---

  getEmail(): Observable<{ status: string; email: string; email_status: string }> {
    return this.http.get<{ status: string; email: string; email_status: string }>('api/email');
  }

  setEmail(email: string): Observable<{ status: string; msg: string; email_status?: string }> {
    return this.http.post<{ status: string; msg: string; email_status?: string }>('api/email', { email });
  }

  deleteEmail(): Observable<{ status: string; msg: string }> {
    return this.http.delete<{ status: string; msg: string }>('api/email').pipe(
      tap(() => { this._email.set(''); this._emailStatus.set('none'); })
    );
  }

  adminSetUserEmail(username: string, email: string): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>(`api/admin/users/${encodeURIComponent(username)}/email`, { email });
  }

  adminDeleteUserEmail(username: string): Observable<{ status: string; msg: string }> {
    return this.http.delete<{ status: string; msg: string }>(`api/admin/users/${encodeURIComponent(username)}/email`);
  }

  // --- SMTP ---

  getSmtpConfig(): Observable<{ status: string; smtp: SmtpConfig }> {
    return this.http.get<{ status: string; smtp: SmtpConfig }>('api/admin/smtp');
  }

  saveSmtpConfig(cfg: Record<string, unknown>): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>('api/admin/smtp', cfg);
  }

  testSmtpConfig(): Observable<{ status: string; msg: string; smtp_status: string }> {
    return this.http.post<{ status: string; msg: string; smtp_status: string }>('api/admin/smtp/test', {});
  }

  detectSmtp(email: string): Observable<{ status: string; detected: boolean; host?: string; port?: number; security?: string; msg?: string }> {
    return this.http.post<{ status: string; detected: boolean; host?: string; port?: number; security?: string; msg?: string }>('api/admin/smtp/detect', { email });
  }

  // --- Password Recovery ---

  checkRecoveryAvailable(): Observable<{ available: boolean }> {
    return this.http.get<{ available: boolean }>('api/recovery-available');
  }

  forgotPassword(username: string, email: string): Observable<{ status: string; msg: string; penalty?: number; attempts_left?: number }> {
    return this.http.post<{ status: string; msg: string; penalty?: number; attempts_left?: number }>('api/forgot-password', { username, email });
  }

  // --- Global Log (admin-group only) ---

  getGlobalLog(): Observable<{ status: string; entries: { url: string; name: string; datetime: string; size: string; filename: string; username: string; file_exists: boolean }[] }> {
    return this.http.get<{ status: string; entries: { url: string; name: string; datetime: string; size: string; filename: string; username: string; file_exists: boolean }[] }>('api/admin/global-log');
  }

  clearGlobalLog(archive: boolean): Observable<{ status: string; archived?: string; deleted?: number; msg?: string }> {
    return this.http.post<{ status: string; archived?: string; deleted?: number; msg?: string }>('api/admin/global-log/clear', { archive });
  }

  getUserSettings(username: string): Observable<{ status: string; settings: Record<string, unknown>; locked_settings: string[] }> {
    return this.http.get<{ status: string; settings: Record<string, unknown>; locked_settings: string[] }>(`api/admin/users/${encodeURIComponent(username)}/settings`);
  }

  setUserSettings(username: string, settings: Record<string, unknown>, lockedSettings: string[]): Observable<{ status: string }> {
    return this.http.put<{ status: string }>(`api/admin/users/${encodeURIComponent(username)}/settings`, { settings, locked_settings: lockedSettings });
  }

  renameUser(oldUsername: string, newUsername: string, moveData = true): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>(`api/admin/users/${encodeURIComponent(oldUsername)}/rename`, { new_username: newUsername, move_data: moveData });
  }

  changeUserHomedir(username: string, newHomedir: string, moveData = true): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>(`api/admin/users/${encodeURIComponent(username)}/change-homedir`, { new_homedir: newHomedir, move_data: moveData });
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  getDownloadLog(): Observable<{ status: string; entries: any[]; has_files: boolean }> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return this.http.get<{ status: string; entries: any[]; has_files: boolean }>('api/log');
  }

  clearDownloadLog(): Observable<{ status: string }> {
    return this.http.post<{ status: string }>('api/log/clear', {});
  }

  deleteLogFiles(filenames: string[], removeFromLog = false): Observable<{ status: string; deleted: string[]; removed_log_entries: number; errors: string[] }> {
    return this.http.post<{ status: string; deleted: string[]; removed_log_entries: number; errors: string[] }>(
      'api/log/delete-file', { filenames, remove_from_log: removeFromLog }
    );
  }

  recoverDownloadLog(): Observable<{ status: string; recovered: number }> {
    return this.http.post<{ status: string; recovered: number }>('api/log/recover', {});
  }

  // --- 2FA API ---

  login2fa(username: string, password: string, totpCode?: string): Observable<LoginResponse> {
    const body: Record<string, string> = { username, password };
    if (totpCode) body['totp_code'] = totpCode;
    return this.http.post<LoginResponse>('api/login', body).pipe(
      tap((res: LoginResponse) => {
        if (res.status === 'ok' && res.token) {
          this._token.set(res.token);
          this._username.set(res.username!);
          this._group.set(res.group!);
          this._mustChangePassword.set(res.must_change_password ?? false);
          this._firstRun.set(res.first_run ?? false);
          localStorage.setItem('Printers_token', res.token);
          localStorage.setItem('Printers_username', res.username!);
          localStorage.setItem('Printers_group', res.group!);
        }
      })
    );
  }

  verify2fa(token2fa: string, totpCode: string): Observable<TwoFactorVerifyResponse> {
    return this.http.post<TwoFactorVerifyResponse>('api/2fa/verify', {
      '2fa_token': token2fa,
      'totp_code': totpCode,
    }).pipe(
      tap((res: TwoFactorVerifyResponse) => {
        if (res.status === 'ok' && res.token) {
          this._token.set(res.token);
          this._username.set(res.username!);
          this._group.set(res.group!);
          this._mustChangePassword.set(res.must_change_password ?? false);
          this._firstRun.set(res.first_run ?? false);
          localStorage.setItem('Printers_token', res.token);
          localStorage.setItem('Printers_username', res.username!);
          localStorage.setItem('Printers_group', res.group!);
        }
      })
    );
  }

  setup2fa(): Observable<TwoFactorSetupResponse> {
    return this.http.post<TwoFactorSetupResponse>('api/2fa/setup', {});
  }

  activate2fa(secret: string, code: string): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>('api/2fa/activate', { secret, code }).pipe(
      tap(res => {
        if (res.status === 'ok') {
          this._clearSession();
        }
      })
    );
  }

  disable2fa(code: string): Observable<{ status: string; msg: string }> {
    return this.http.post<{ status: string; msg: string }>('api/2fa/disable', { code }).pipe(
      tap(res => {
        if (res.status === 'ok') {
          this._clearSession();
        }
      })
    );
  }

  private _clearSession() {
    this._token.set(null);
    this._username.set(null);
    this._group.set(null);
    this._mustChangePassword.set(false);
    this._firstRun.set(false);
    this._hasCookies.set(false);
    this._totpEnabled.set(false);
    this._email.set('');
    this._emailStatus.set('none');
    this._smtpConfigured.set(false);
    this._lockedSettings.set([]);
    this._storageQuota.set(0);
    this._storageUsed.set(0);
    this._storageReserved.set(0);
    this._storageQuotaStr.set('0');
    localStorage.removeItem('Printers_token');
    localStorage.removeItem('Printers_username');
    localStorage.removeItem('Printers_group');
  }
}
