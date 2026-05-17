import { Component, inject, input, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { FontAwesomeModule } from '@fortawesome/angular-fontawesome';
import { faTimes, faShieldAlt, faQrcode, faKeyboard } from '@fortawesome/free-solid-svg-icons';
import { AuthService } from '../services/auth.service';

@Component({
  selector: 'app-two-factor-setup',
  standalone: true,
  imports: [FormsModule, FontAwesomeModule],
  template: `
    <div class="modal fade show d-block" style="background:rgba(0,0,0,0.5);z-index:10000">
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header py-2">
            <h5 class="modal-title">
              <fa-icon [icon]="faShieldAlt" class="me-2" />
              @if (mode() === 'enable') { Enable Two-Factor Authentication }
              @else { Disable Two-Factor Authentication }
            </h5>
            <button class="btn btn-sm btn-outline-secondary" (click)="closed.emit()" title="Close">
              <fa-icon [icon]="faTimes" />
            </button>
          </div>
          <div class="modal-body">
            @if (mode() === 'enable') {
              @if (!setupData) {
                <div class="text-center py-3">
                  <span class="spinner-border spinner-border-sm me-1"></span> Generating 2FA secret...
                </div>
              } @else if (activated) {
                <div class="alert alert-success">
                  <strong>2FA activated successfully!</strong> You will be logged out from all devices.
                </div>
              } @else {
                <p class="small text-muted mb-2">Scan the QR code with your authenticator app (Google Authenticator, Authy, etc.), then enter the 6-digit code to verify.</p>

                <!-- QR / Text toggle -->
                <div class="text-center mb-3">
                  @if (showQr) {
                    <img [src]="setupData.qr_code" alt="2FA QR Code" class="img-fluid" style="max-width:220px">
                    <div class="mt-2">
                      <a href="javascript:void(0)" class="small" (click)="showQr = false">
                        <fa-icon [icon]="faKeyboard" class="me-1" /> Switch to text
                      </a>
                    </div>
                  } @else {
                    <div class="p-3 bg-light border rounded">
                      <label class="form-label small fw-semibold mb-1">Secret key (enter manually):</label>
                      <div class="font-monospace fw-bold" style="font-size:1.1em;letter-spacing:2px;word-break:break-all">
                        {{ setupData.secret }}
                      </div>
                    </div>
                    <div class="mt-2">
                      <a href="javascript:void(0)" class="small" (click)="showQr = true">
                        <fa-icon [icon]="faQrcode" class="me-1" /> Switch to QR code
                      </a>
                    </div>
                  }
                </div>

                <!-- Verification code input -->
                @if (errorMsg) {
                  <div class="alert alert-danger py-1 small">{{ errorMsg }}</div>
                }
                <div class="d-flex gap-2 align-items-end">
                  <div class="flex-grow-1">
                    <label class="form-label small mb-0">Verification code</label>
                    <input type="text" class="form-control" [(ngModel)]="verifyCode"
                      placeholder="Enter 6-digit code" maxlength="6" inputmode="numeric"
                      pattern="[0-9]*" autocomplete="one-time-code"
                      (keydown.enter)="activate()">
                  </div>
                  <button class="btn btn-success" [disabled]="verifyCode.length < 6 || loading"
                    (click)="activate()">
                    @if (loading) {
                      <span class="spinner-border spinner-border-sm me-1"></span>
                    }
                    Activate
                  </button>
                </div>
              }
            } @else {
              <!-- Disable mode -->
              @if (deactivated) {
                <div class="alert alert-success">
                  <strong>2FA disabled successfully!</strong> You will be logged out from all devices.
                </div>
              } @else {
                <p class="small text-muted mb-2">Enter your current 2FA code to disable two-factor authentication.</p>
                @if (errorMsg) {
                  <div class="alert alert-danger py-1 small">{{ errorMsg }}</div>
                }
                <div class="d-flex gap-2 align-items-end">
                  <div class="flex-grow-1">
                    <label class="form-label small mb-0">Current 2FA code</label>
                    <input type="text" class="form-control" [(ngModel)]="verifyCode"
                      placeholder="Enter 6-digit code" maxlength="6" inputmode="numeric"
                      pattern="[0-9]*" autocomplete="one-time-code"
                      (keydown.enter)="deactivate()">
                  </div>
                  <button class="btn btn-danger" [disabled]="verifyCode.length < 6 || loading"
                    (click)="deactivate()">
                    @if (loading) {
                      <span class="spinner-border spinner-border-sm me-1"></span>
                    }
                    Disable 2FA
                  </button>
                </div>
              }
            }
          </div>
          <div class="modal-footer py-1">
            <button class="btn btn-sm btn-secondary" (click)="closed.emit()">Close</button>
          </div>
        </div>
      </div>
    </div>
  `,
})
export class TwoFactorSetupComponent {
  private auth = inject(AuthService);
  readonly closed = output<void>();

  readonly mode = input<'enable' | 'disable'>('enable');
  setupData: { secret: string; qr_code: string; uri: string } | null = null;
  showQr = true;
  verifyCode = '';
  errorMsg = '';
  loading = false;
  activated = false;
  deactivated = false;

  faTimes = faTimes;
  faShieldAlt = faShieldAlt;
  faQrcode = faQrcode;
  faKeyboard = faKeyboard;

  ngOnInit() {
    if (this.mode() === 'enable') {
      this.auth.setup2fa().subscribe({
        next: (res) => {
          if (res.status === 'ok') {
            this.setupData = { secret: res.secret, qr_code: res.qr_code, uri: res.uri };
          } else {
            this.errorMsg = 'Failed to generate 2FA setup.';
          }
        },
        error: (err: { error?: { msg?: string } }) => {
          this.errorMsg = err?.error?.msg || 'Failed to generate 2FA setup.';
        },
      });
    }
  }

  activate() {
    if (!this.setupData || this.verifyCode.length < 6) return;
    this.loading = true;
    this.errorMsg = '';
    this.auth.activate2fa(this.setupData.secret, this.verifyCode).subscribe({
      next: (res) => {
        this.loading = false;
        if (res.status === 'ok') {
          this.activated = true;
        } else {
          this.errorMsg = res.msg || 'Activation failed.';
        }
      },
      error: (err: { error?: { msg?: string } }) => {
        this.loading = false;
        this.errorMsg = err?.error?.msg || 'Invalid verification code. Try again.';
      },
    });
  }

  deactivate() {
    if (this.verifyCode.length < 6) return;
    this.loading = true;
    this.errorMsg = '';
    this.auth.disable2fa(this.verifyCode).subscribe({
      next: (res) => {
        this.loading = false;
        if (res.status === 'ok') {
          this.deactivated = true;
        } else {
          this.errorMsg = res.msg || 'Deactivation failed.';
        }
      },
      error: (err: { error?: { msg?: string } }) => {
        this.loading = false;
        this.errorMsg = err?.error?.msg || 'Invalid 2FA code.';
      },
    });
  }
}
