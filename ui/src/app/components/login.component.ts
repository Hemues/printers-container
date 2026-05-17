import { Component, inject, OnInit, output } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../services/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="login-overlay">
      <div class="login-card">
        <div class="text-center mb-4">
          <img src="assets/icons/android-chrome-192x192.png" alt="Printers" height="64">
          <h3 class="mt-2">Printers</h3>
          @if (!show2fa) {
            <p class="text-muted">Sign in to continue</p>
          } @else {
            <p class="text-muted">Two-Factor Authentication</p>
          }
        </div>

        @if (!show2fa) {
          <!-- Normal login form -->
          @if (errorMsg) {
            <div class="alert alert-danger py-2">{{ errorMsg }}</div>
          }
          <form (ngSubmit)="doLogin()">
            <div class="mb-3">
              <label class="form-label" for="username">Username</label>
              <input type="text" id="username" class="form-control" name="username"
                [(ngModel)]="username" autocomplete="username" autofocus required>
            </div>
            <div class="mb-3">
              <label class="form-label" for="password">Password</label>
              <input type="password" id="password" class="form-control" name="password"
                [(ngModel)]="password" autocomplete="current-password" required>
            </div>
            <button type="submit" class="btn btn-primary w-100" [disabled]="loading">
              @if (loading) {
                <span class="spinner-border spinner-border-sm me-1"></span>
              }
              Sign In
            </button>
          </form>
          @if (recoveryAvailable) {
            <div class="text-center mt-3">
              <a href="javascript:void(0)" class="small text-muted" (click)="openForgotPassword()">Forgot Password?</a>
            </div>
          }
        } @else {
          <!-- 2FA verification step -->
          <div class="text-center mb-2" [class.text-danger]="countdown <= 10">
            <strong>{{ countdown }}</strong> seconds left
          </div>
          @if (twoFaError) {
            <div class="alert alert-danger py-2">{{ twoFaError }}</div>
          }
          <form (ngSubmit)="do2faVerify()">
            <div class="mb-3">
              <label class="form-label" for="totp_code">2FA Code</label>
              <div class="d-flex gap-2">
                <input type="text" id="totp_code" class="form-control" name="totp_code"
                  [(ngModel)]="totpCode" placeholder="Enter 6-digit code"
                  maxlength="6" inputmode="numeric" pattern="[0-9]*"
                  autocomplete="one-time-code" autofocus required>
                <button type="submit" class="btn btn-primary" [disabled]="totpCode.length < 6 || twoFaLoading">
                  @if (twoFaLoading) {
                    <span class="spinner-border spinner-border-sm"></span>
                  } @else {
                    Submit
                  }
                </button>
              </div>
            </div>
          </form>
          <div class="text-center">
            <a href="javascript:void(0)" class="small text-muted" (click)="cancel2fa()">Back to login</a>
          </div>
        }
      </div>

      <!-- Forgot Password popup -->
      @if (showForgotPassword) {
        <div class="forgot-backdrop" (click)="closeForgotPassword()"></div>
        <div class="forgot-card">
          <h5 class="mb-3">Password Recovery</h5>
          <p class="text-muted small mb-3">Enter your username and verified email address. A recovery link will be sent to your email.</p>
          @if (forgotSuccess) {
            <div class="alert alert-success py-2">{{ forgotSuccess }}</div>
          }
          @if (forgotError) {
            <div class="alert alert-danger py-2">{{ forgotError }}</div>
          }
          @if (!forgotSuccess) {
            <form (ngSubmit)="submitForgotPassword()">
              <div class="mb-3">
                <label class="form-label" for="forgot_username">Username</label>
                <input type="text" id="forgot_username" class="form-control" name="forgot_username"
                  [(ngModel)]="forgotUsername" autocomplete="username" required>
              </div>
              <div class="mb-3">
                <label class="form-label" for="forgot_email">Email</label>
                <input type="email" id="forgot_email" class="form-control" name="forgot_email"
                  [(ngModel)]="forgotEmail" autocomplete="email" required>
              </div>
              <div class="d-flex gap-2">
                <button type="submit" class="btn btn-primary flex-fill" [disabled]="forgotLoading || !forgotUsername || !forgotEmail">
                  @if (forgotLoading) {
                    <span class="spinner-border spinner-border-sm me-1"></span>
                  }
                  Send Recovery Email
                </button>
                <button type="button" class="btn btn-outline-secondary" (click)="closeForgotPassword()">Cancel</button>
              </div>
            </form>
          } @else {
            <button type="button" class="btn btn-outline-secondary w-100" (click)="closeForgotPassword()">Close</button>
          }
        </div>
      }
      <div class="login-footer">
        @if (version) {
          <span class="version-label">container</span>
          <span class="version-value">{{ version }}</span>
        }
      </div>
    </div>
  `,
  styles: [`
    .login-overlay {
      position: fixed;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      background: var(--bs-body-bg, #f5f5f5);
      z-index: 9999;
    }
    .login-card {
      width: 100%;
      max-width: 380px;
      padding: 2rem;
      border-radius: 12px;
      background: var(--bs-body-bg);
      border: 1px solid var(--bs-border-color);
      box-shadow: 0 4px 24px rgba(0,0,0,0.1);
    }
    .forgot-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.5);
      z-index: 10000;
    }
    .forgot-card {
      position: fixed;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      width: 100%;
      max-width: 400px;
      padding: 2rem;
      border-radius: 12px;
      background: var(--bs-body-bg);
      border: 1px solid var(--bs-border-color);
      box-shadow: 0 4px 24px rgba(0,0,0,0.2);
      z-index: 10001;
    }
    .login-footer {
      position: fixed;
      bottom: 1rem;
      left: 0;
      right: 0;
      text-align: center;
      font-size: 0.8rem;
      opacity: 0.6;
    }
    .login-footer .version-label {
      text-transform: uppercase;
      letter-spacing: 0.5px;
      font-size: 0.7rem;
      margin-right: 6px;
    }
    .login-footer .version-value {
      font-family: monospace;
      padding: 2px 6px;
      background: rgba(128,128,128,0.15);
      border-radius: 4px;
    }
  `]
})
export class LoginComponent implements OnInit {
  private auth = inject(AuthService);
  private http = inject(HttpClient);
  readonly loggedIn = output<void>();

  version: string | null = null;

  username = '';
  password = '';
  loading = false;
  errorMsg = '';

  // 2FA state
  show2fa = false;
  twoFaToken = '';
  totpCode = '';
  twoFaError = '';
  twoFaLoading = false;
  countdown = 30;
  private countdownTimer: ReturnType<typeof setInterval> | null = null;

  // Forgot password state
  recoveryAvailable = false;
  showForgotPassword = false;
  forgotUsername = '';
  forgotEmail = '';
  forgotLoading = false;
  forgotError = '';
  forgotSuccess = '';
  private forgotAutoCloseTimer: ReturnType<typeof setTimeout> | null = null;

  ngOnInit() {
    this.auth.checkRecoveryAvailable().subscribe({
      next: (res) => this.recoveryAvailable = res.available,
      error: () => this.recoveryAvailable = false
    });
    this.http.get<{ version: string }>('api/version').subscribe({
      next: (r) => this.version = r.version,
      error: () => { /* ignore */ }
    });
  }

  openForgotPassword() {
    this.showForgotPassword = true;
    this.forgotUsername = '';
    this.forgotEmail = '';
    this.forgotError = '';
    this.forgotSuccess = '';
  }

  closeForgotPassword() {
    this.showForgotPassword = false;
    if (this.forgotAutoCloseTimer) {
      clearTimeout(this.forgotAutoCloseTimer);
      this.forgotAutoCloseTimer = null;
    }
  }

  submitForgotPassword() {
    this.forgotLoading = true;
    this.forgotError = '';
    this.forgotSuccess = '';
    this.auth.forgotPassword(this.forgotUsername, this.forgotEmail).subscribe({
      next: (res) => {
        this.forgotLoading = false;
        if (res.status === 'ok') {
          this.forgotSuccess = res.msg || 'Recovery email has been sent. Check your inbox.';
          this.forgotAutoCloseTimer = setTimeout(() => this.closeForgotPassword(), 5000);
        } else {
          this.forgotError = res.msg || 'Recovery request failed.';
        }
      },
      error: (err: { status?: number; error?: { msg?: string; attempts_left?: number; penalty?: number } }) => {
        this.forgotLoading = false;
        const body = err?.error;
        if (body?.attempts_left !== undefined && body.attempts_left <= 0) {
          // 5th attempt exhausted — close popup, show error on main page
          this.showForgotPassword = false;
          this.errorMsg = body?.msg || 'Too many recovery attempts. Please try again later.';
        } else if (body?.penalty) {
          // Rate-limited — close popup, show penalty on main page
          this.showForgotPassword = false;
          this.errorMsg = body?.msg || `Too many attempts. Please wait ${body.penalty} seconds.`;
        } else {
          this.forgotError = body?.msg || 'Recovery request failed.';
        }
      }
    });
  }

  doLogin() {
    this.loading = true;
    this.errorMsg = '';
    this.auth.login(this.username, this.password).subscribe({
      next: (res) => {
        this.loading = false;
        if (res.status === 'ok') {
          this.loggedIn.emit();
        } else if (res.status === '2fa_required') {
          // Show 2FA verification step
          this.twoFaToken = res['2fa_token'] || '';
          this.show2fa = true;
          this.twoFaError = '';
          this.totpCode = '';
          this.startCountdown();
        } else {
          this.errorMsg = res.msg || 'Login failed.';
        }
      },
      error: (err: { status?: number; error?: { msg?: string; '2fa_penalty'?: number; status?: string; '2fa_token'?: string } }) => {
        this.loading = false;
        const body = err?.error;
        if (body?.status === '2fa_required') {
          this.twoFaToken = body['2fa_token'] || '';
          this.show2fa = true;
          this.twoFaError = '';
          this.totpCode = '';
          this.startCountdown();
        } else if (body?.['2fa_penalty']) {
          this.errorMsg = body?.msg || `Too many failed 2FA attempts. Wait ${body['2fa_penalty']} seconds.`;
        } else {
          this.errorMsg = body?.msg || 'Unable to reach server.';
        }
      }
    });
  }

  do2faVerify() {
    if (this.totpCode.length < 6) return;
    this.twoFaLoading = true;
    this.twoFaError = '';
    this.auth.verify2fa(this.twoFaToken, this.totpCode).subscribe({
      next: (res) => {
        this.twoFaLoading = false;
        if (res.status === 'ok') {
          this.stopCountdown();
          this.loggedIn.emit();
        } else {
          this.twoFaError = res.msg || 'Verification failed.';
          this.totpCode = '';
          // Restart countdown on retry
          this.restartCountdown();
        }
      },
      error: (err: { error?: { msg?: string; '2fa_retries_left'?: number; '2fa_penalty'?: number; '2fa_failed'?: boolean } }) => {
        this.twoFaLoading = false;
        const body = err?.error;
        if (body?.['2fa_failed'] || body?.['2fa_penalty']) {
          // Max retries reached or penalty applied
          this.stopCountdown();
          this.show2fa = false;
          this.errorMsg = body?.msg || 'Failed 2FA authentication.';
        } else {
          this.twoFaError = body?.msg || 'Invalid 2FA code.';
          this.totpCode = '';
          this.restartCountdown();
        }
      }
    });
  }

  cancel2fa() {
    this.stopCountdown();
    this.show2fa = false;
    this.twoFaToken = '';
    this.totpCode = '';
    this.twoFaError = '';
  }

  private startCountdown() {
    this.countdown = 30;
    this.stopCountdown();
    this.countdownTimer = setInterval(() => {
      this.countdown--;
      if (this.countdown <= 0) {
        this.stopCountdown();
        this.show2fa = false;
        this.errorMsg = '2FA verification timed out. Please login again.';
      }
    }, 1000);
  }

  private restartCountdown() {
    this.countdown = 30;
  }

  private stopCountdown() {
    if (this.countdownTimer) {
      clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    }
  }
}
