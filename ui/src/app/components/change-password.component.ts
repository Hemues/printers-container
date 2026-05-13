import { Component, inject, input, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../services/auth.service';

@Component({
  selector: 'app-change-password',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="modal fade show d-block" style="background:rgba(0,0,0,0.4); z-index: 9999">
      <div class="modal-dialog modal-sm">
        <div class="modal-content">
          <div class="modal-header py-2">
            <h6 class="modal-title">Change Password</h6>
            @if (!forceChange()) {
              <button class="btn-close btn-close-sm" (click)="closed.emit()"></button>
            }
          </div>
          <div class="modal-body">
            @if (forceChange()) {
              <div class="alert alert-warning py-2 small">
                You must change your password before continuing.
              </div>
            }
            @if (errorMsg) {
              <div class="alert alert-danger py-2 small">{{ errorMsg }}</div>
            }
            @if (successMsg) {
              <div class="alert alert-success py-2 small">{{ successMsg }}</div>
            }
            <div class="mb-2">
              <label class="form-label small mb-0">Current Password</label>
              <input type="password" class="form-control form-control-sm" [(ngModel)]="currentPw"
                autocomplete="current-password">
            </div>
            <div class="mb-2">
              <label class="form-label small mb-0">New Password</label>
              <input type="password" class="form-control form-control-sm" [(ngModel)]="newPw"
                autocomplete="new-password">
            </div>
            <div class="mb-2">
              <label class="form-label small mb-0">Confirm New Password</label>
              <input type="password" class="form-control form-control-sm" [(ngModel)]="newPwConfirm"
                autocomplete="new-password">
            </div>
          </div>
          <div class="modal-footer py-1">
            @if (!forceChange()) {
              <button class="btn btn-sm btn-secondary" (click)="closed.emit()">Cancel</button>
            }
            <button class="btn btn-sm btn-primary" [disabled]="loading || !currentPw || !newPw || !newPwConfirm"
              (click)="submit()">
              @if (loading) {
                <span class="spinner-border spinner-border-sm me-1"></span>
              }
              Change Password
            </button>
          </div>
        </div>
      </div>
    </div>
  `,
})
export class ChangePasswordComponent {
  private auth = inject(AuthService);
  readonly forceChange = input(false);
  readonly closed = output<void>();
  readonly passwordChanged = output<void>();

  currentPw = '';
  newPw = '';
  newPwConfirm = '';
  loading = false;
  errorMsg = '';
  successMsg = '';

  submit() {
    this.errorMsg = '';
    this.successMsg = '';
    if (this.newPw !== this.newPwConfirm) {
      this.errorMsg = 'New passwords do not match.';
      return;
    }
    this.loading = true;
    this.auth.changePassword(this.currentPw, this.newPw, this.newPwConfirm).subscribe({
      next: (res: { status: string; msg: string }) => {
        this.loading = false;
        if (res.status === 'ok') {
          this.successMsg = 'Password changed. You will be logged out.';
          setTimeout(() => this.passwordChanged.emit(), 1500);
        } else {
          this.errorMsg = res.msg;
        }
      },
      error: (err: { error?: { msg?: string } }) => {
        this.loading = false;
        this.errorMsg = err?.error?.msg || 'Error changing password.';
      }
    });
  }
}
