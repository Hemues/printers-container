import { Component, inject, OnInit, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { FontAwesomeModule } from '@fortawesome/angular-fontawesome';
import { faTrashAlt, faKey, faUserPlus, faLock, faUnlock, faTimes, faArrowLeft, faCog, faSave, faShieldAlt, faToggleOn, faToggleOff, faEnvelope, faClipboardList, faPrint, faPlus, faChartBar } from '@fortawesome/free-solid-svg-icons';
import { AuthService, UserRecord } from '../services/auth.service';

interface SettingEntry {
  key: string;
  value: string;
  locked: boolean;
  isOverride: boolean;  // true if user has explicitly set this value
}

@Component({
  selector: 'app-admin-panel',
  standalone: true,
  imports: [FormsModule, FontAwesomeModule],
  template: `
    <div class="admin-overlay">
      <div class="admin-panel">
        <div class="admin-header d-flex justify-content-between align-items-center mb-3">
          <h4 class="mb-0">
            <button class="btn btn-sm btn-outline-secondary me-2" (click)="close.emit()">
              <fa-icon [icon]="faArrowLeft" />
            </button>
            Admin Panel — User Management
          </h4>
          <div class="d-flex align-items-center gap-2">
            @if (auth.isAdminGroup()) {
              <button class="btn btn-sm btn-outline-info" (click)="openSmtp()" title="SMTP Email Configuration">
                <fa-icon [icon]="faEnvelope" />
              </button>
              <button class="btn btn-sm btn-outline-secondary" (click)="openPrinters()" title="CUPS Printers">
                <fa-icon [icon]="faPrint" />
              </button>
              <button class="btn btn-sm btn-outline-secondary" (click)="openAdminStats()" title="Global Print Stats">
                <fa-icon [icon]="faChartBar" />
              </button>
              <button class="btn btn-sm btn-outline-info" (click)="openGlobalLog()" title="Global Print Log">
                <fa-icon [icon]="faClipboardList" />
              </button>
            }
            <button class="btn btn-sm btn-outline-secondary" (click)="close.emit()">
              <fa-icon [icon]="faTimes" />
            </button>
          </div>
        </div>

        @if (statusMsg) {
          <div class="alert py-2" [class.alert-success]="!statusIsError" [class.alert-danger]="statusIsError">
            {{ statusMsg }}
          </div>
        }

        <!-- Add user form -->
        <div class="card mb-3">
          <div class="card-body py-2">
            <form class="row g-2 align-items-end" (ngSubmit)="addUser()">
              <div class="col-md-3">
                <label class="form-label mb-0 small">Username</label>
                <input type="text" class="form-control form-control-sm" [(ngModel)]="newUsername" name="newUsername" required>
              </div>
              <div class="col-md-3">
                <label class="form-label mb-0 small">Password</label>
                <input type="text" class="form-control form-control-sm" [(ngModel)]="newPassword" name="newPassword" required>
              </div>
              <div class="col-md-3">
                <label class="form-label mb-0 small">Group</label>
                <select class="form-select form-select-sm" [(ngModel)]="newGroup" name="newGroup">
                  <option value="users-group">Users</option>
                  <option value="useradmin-group">UserAdmins</option>
                  @if (auth.isAdminGroup()) {
                    <option value="admin-group">Administrators</option>
                  }
                </select>
              </div>
              <div class="col-md-3">
                <button type="submit" class="btn btn-sm btn-success w-100" [disabled]="!newUsername || !newPassword">
                  <fa-icon [icon]="faUserPlus" class="me-1" /> Add User
                </button>
              </div>
            </form>
          </div>
        </div>

        <!-- Users table -->
        <div class="table-responsive">
          <table class="table table-sm table-hover align-middle mb-0">
            <thead>
              <tr>
                <th>Username</th>
                <th>Group</th>
                <th>Home Directory</th>
                <th>Email</th>
                <th class="text-center">Cookies</th>
                <th class="text-center">2FA</th>
                <th class="text-center">Must Change PW</th>
                <th>Actions</th>
              </tr>
              <tr>
                <td>
                  <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                    [(ngModel)]="filterUsername" (ngModelChange)="applyUserFilters()">
                </td>
                <td>
                  <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                    [(ngModel)]="filterGroup" (ngModelChange)="applyUserFilters()">
                </td>
                <td>
                  <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                    [(ngModel)]="filterHomedir" (ngModelChange)="applyUserFilters()">
                </td>
                <td>
                  <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                    [(ngModel)]="filterEmail" (ngModelChange)="applyUserFilters()">
                </td>
                <td>
                  <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                    [(ngModel)]="filterCookies" (ngModelChange)="applyUserFilters()">
                </td>
                <td>
                  <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                    [(ngModel)]="filter2fa" (ngModelChange)="applyUserFilters()">
                </td>
                <td>
                  <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                    [(ngModel)]="filterChangePw" (ngModelChange)="applyUserFilters()">
                </td>
                <td></td>
              </tr>
            </thead>
            <tbody>
              @for (user of filteredUsers; track user.username) {
                <tr [class.table-secondary]="!user.enabled">
                  <td [class.text-decoration-line-through]="!user.enabled">
                    {{ user.username }}
                  </td>
                  <td [class.text-decoration-line-through]="!user.enabled">
                    <select class="form-select form-select-sm" [ngModel]="user.group"
                      (ngModelChange)="changeGroup(user, $event)"
                      [disabled]="user.username === 'admin' || !canManage(user)"
                      style="width:140px">
                      <option value="users-group">Users</option>
                      <option value="useradmin-group">UserAdmins</option>
                      @if (auth.isAdminGroup()) {
                        <option value="admin-group">Administrators</option>
                      }
                    </select>
                  </td>
                  <td class="small text-muted" [class.text-decoration-line-through]="!user.enabled">{{ user.homedir }}</td>
                  <td class="small" [class.text-decoration-line-through]="!user.enabled">
                    @if (user.email) {
                      @if (user.email_status === 'verified') {
                        <span class="badge bg-success" title="Verified">{{ user.email }}</span>
                      } @else {
                        <span class="badge bg-danger" title="Pending verification">{{ user.email }}</span>
                      }
                    } @else {
                      <span class="badge bg-secondary">None</span>
                    }
                  </td>
                  <td class="text-center" [class.text-decoration-line-through]="!user.enabled">
                    @if (user.has_cookies) {
                      <span class="badge bg-success">Yes</span>
                    } @else {
                      <span class="badge bg-secondary">No</span>
                    }
                  </td>
                  <td class="text-center" [class.text-decoration-line-through]="!user.enabled">
                    @if (user.totp_enabled) {
                      <span class="badge bg-success">On</span>
                    } @else {
                      <span class="badge bg-secondary">Off</span>
                    }
                  </td>
                  <td class="text-center" [class.text-decoration-line-through]="!user.enabled">
                    @if (user.must_change_password) {
                      <span class="badge bg-warning text-dark">Yes</span>
                    } @else {
                      <span class="badge bg-secondary">No</span>
                    }
                  </td>
                  <td>
                    <div class="btn-group btn-group-sm">
                      <button class="btn btn-outline-info" title="User Settings" (click)="openSettings(user)">
                        <fa-icon [icon]="faCog" />
                      </button>
                      <button class="btn btn-outline-warning" title="Reset Password" (click)="openResetPassword(user)">
                        <fa-icon [icon]="faKey" />
                      </button>
                      <button
                        [class]="user.totp_enabled ? 'btn btn-outline-success' : 'btn btn-outline-secondary'"
                        [title]="user.totp_enabled ? 'Disable 2FA' : '2FA not enabled'"
                        [disabled]="!user.totp_enabled"
                        (click)="adminDisable2fa(user)">
                        <fa-icon [icon]="faShieldAlt" />
                      </button>
                      <button
                        [class]="user.enabled ? 'btn btn-outline-success' : 'btn btn-outline-danger'"
                        [title]="user.enabled ? 'Disable User' : 'Enable User'"
                        [disabled]="user.username === 'admin'"
                        (click)="toggleEnabled(user)">
                        <fa-icon [icon]="user.enabled ? faToggleOn : faToggleOff" />
                      </button>
                      <button class="btn btn-outline-danger" title="Delete User"
                        [disabled]="user.username === 'admin'"
                        (click)="deleteUser(user)">
                        <fa-icon [icon]="faTrashAlt" />
                      </button>
                    </div>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>

        <!-- Reset Password Modal -->
        @if (resetTarget) {
          <div class="modal fade show d-block" style="background:rgba(0,0,0,0.4)">
            <div class="modal-dialog modal-sm">
              <div class="modal-content">
                <div class="modal-header py-2">
                  <h6 class="modal-title">Reset Password — {{ resetTarget.username }}</h6>
                  <button class="btn-close btn-close-sm" (click)="resetTarget = null"></button>
                </div>
                <div class="modal-body">
                  <input type="text" class="form-control form-control-sm" [(ngModel)]="resetNewPassword"
                    placeholder="New password">
                </div>
                <div class="modal-footer py-1">
                  <button class="btn btn-sm btn-secondary" (click)="resetTarget = null">Cancel</button>
                  <button class="btn btn-sm btn-warning" [disabled]="!resetNewPassword" (click)="doResetPassword()">Reset</button>
                </div>
              </div>
            </div>
          </div>
        }

        <!-- User Settings Modal -->
        @if (settingsTarget) {
          <div class="modal fade show d-block" style="background:rgba(0,0,0,0.4)">
            <div class="modal-dialog modal-lg">
              <div class="modal-content">
                <div class="modal-header py-2">
                  <h6 class="modal-title">Settings — {{ settingsTarget.username }}</h6>
                  <button class="btn-close btn-close-sm" (click)="settingsTarget = null"></button>
                </div>
                <div class="modal-body p-0">
                  @if (settingsLoading) {
                    <div class="text-center py-4">
                      <span class="spinner-border spinner-border-sm"></span> Loading settings…
                    </div>
                  } @else {
                    @if (settingsStatus) {
                      <div class="alert py-1 mx-3 mt-2 mb-0" [class.alert-success]="!settingsStatusErr" [class.alert-danger]="settingsStatusErr">
                        {{ settingsStatus }}
                      </div>
                    }
                    <!-- Username & Home Directory -->
                    <div class="px-3 pt-3 pb-2">
                      <div class="row g-2">
                        <div class="col-md-6">
                          <label class="form-label mb-0 small fw-semibold">Username</label>
                          <input type="text" class="form-control form-control-sm"
                            [(ngModel)]="editUsername"
                            (ngModelChange)="onSettingChanged()"
                            [disabled]="settingsTarget.username === currentAdminUsername">
                          @if (settingsTarget.username === currentAdminUsername) {
                            <small class="text-muted">Cannot rename yourself</small>
                          }
                        </div>
                        <div class="col-md-6">
                          <label class="form-label mb-0 small fw-semibold">Home Directory</label>
                          <input type="text" class="form-control form-control-sm"
                            [(ngModel)]="editHomedir"
                            (ngModelChange)="onSettingChanged()">
                        </div>
                      </div>
                      <div class="row g-2 mt-1">
                        <div class="col-md-6">
                          <label class="form-label mb-0 small fw-semibold">Email</label>
                          <div class="input-group input-group-sm">
                            <input type="email" class="form-control form-control-sm"
                              [(ngModel)]="editEmail"
                              placeholder="user&#64;example.com">
                            @if (editEmail && editEmail !== (settingsTarget.email || '')) {
                              <button class="btn btn-outline-warning btn-sm" (click)="adminSetEmail()" title="Set email & send verification">
                                Set
                              </button>
                            }
                            @if (settingsTarget.email) {
                              <button class="btn btn-outline-danger btn-sm" (click)="adminDeleteEmail()" title="Remove email">
                                <fa-icon [icon]="faTrashAlt" />
                              </button>
                            }
                          </div>
                          @if (settingsTarget.email) {
                            @if (settingsTarget.email_status === 'verified') {
                              <small class="text-danger">Verified</small>
                            } @else {
                              <small class="text-warning">Pending verification</small>
                            }
                          }
                        </div>
                        <div class="col-md-6">
                          <label class="form-label mb-0 small fw-semibold">Group</label>
                          <select class="form-select form-select-sm" [ngModel]="settingsTarget.group"
                            (ngModelChange)="changeGroupInSettings($event)"
                            [disabled]="settingsTarget.username === 'admin' || !canManage(settingsTarget)">
                            <option value="users-group">Users</option>
                            <option value="useradmin-group">UserAdmins</option>
                            @if (auth.isAdminGroup()) {
                              <option value="admin-group">Administrators</option>
                            }
                          </select>
                        </div>
                      </div>
                    </div>
                    <hr class="my-2 mx-3">
                    <div class="table-responsive">
                      <table class="table table-sm table-hover align-middle mb-0">
                        <thead>
                          <tr>
                            <th style="width:40px" class="text-center" title="Lock setting for user">
                              <fa-icon [icon]="faLock" />
                            </th>
                            <th>Setting</th>
                            <th>Value</th>
                          </tr>
                        </thead>
                        <tbody>
                          @for (entry of settingsEntries; track entry.key) {
                            <tr [class.table-secondary]="entry.locked">
                              <td class="text-center">
                                <input type="checkbox" class="form-check-input"
                                  [(ngModel)]="entry.locked"
                                  (ngModelChange)="onSettingChanged()"
                                  title="{{ entry.locked ? 'Unlock' : 'Lock' }} this setting">
                              </td>
                              <td class="small fw-semibold text-nowrap">
                                {{ entry.key }}
                                @if (!entry.isOverride) {
                                  <span class="badge bg-secondary ms-1" style="font-size:0.65em">global</span>
                                } @else {
                                  <span class="badge bg-info ms-1" style="font-size:0.65em">user</span>
                                }
                              </td>
                              <td>
                                <input type="text" class="form-control form-control-sm"
                                  [(ngModel)]="entry.value"
                                  (ngModelChange)="onSettingChanged()"
                                  [disabled]="entry.locked"
                                  [class.text-muted]="entry.locked">
                              </td>
                            </tr>
                          }
                        </tbody>
                      </table>
                    </div>
                  }
                </div>
                <div class="modal-footer py-1">
                  <button class="btn btn-sm btn-secondary" (click)="settingsTarget = null">Close</button>
                  @if (needsRenameOrHomedir) {
                    <button class="btn btn-sm btn-warning" [disabled]="!settingsDirty || settingsLoading" (click)="saveSettings(true)">
                      <fa-icon [icon]="faSave" class="me-1" /> Save &amp; Move
                    </button>
                  }
                  <button class="btn btn-sm btn-primary" [disabled]="!settingsDirty || settingsLoading" (click)="saveSettings(false)">
                    <fa-icon [icon]="faSave" class="me-1" /> Save
                  </button>
                </div>
              </div>
            </div>
          </div>
        }

        <!-- CUPS Printers Modal -->
        @if (showPrinters) {
          <div class="modal fade show d-block" style="background:rgba(0,0,0,0.4)">
            <div class="modal-dialog modal-lg">
              <div class="modal-content">
                <div class="modal-header py-2">
                  <h6 class="modal-title">CUPS Printers</h6>
                  <button class="btn-close btn-close-sm" (click)="showPrinters = false"></button>
                </div>
                <div class="modal-body">
                  @if (printersStatusMsg) {
                    <div class="alert py-2" [class.alert-success]="!printersStatusErr" [class.alert-danger]="printersStatusErr">
                      {{ printersStatusMsg }}
                    </div>
                  }
                  <!-- Add printer form -->
                  <form class="row g-2 align-items-end mb-3" (ngSubmit)="addPrinter()">
                    <div class="col-md-4">
                      <label class="form-label mb-0 small">Printer name</label>
                      <input type="text" class="form-control form-control-sm" [(ngModel)]="newPrinterName" name="newPrinterName" placeholder="HP_LaserJet" required>
                    </div>
                    <div class="col-md-5">
                      <label class="form-label mb-0 small">URI (e.g. socket://192.168.1.5:9100)</label>
                      <input type="text" class="form-control form-control-sm" [(ngModel)]="newPrinterUri" name="newPrinterUri" placeholder="socket://192.168.1.5:9100" required>
                    </div>
                    <div class="col-md-3">
                      <button type="submit" class="btn btn-sm btn-success w-100" [disabled]="!newPrinterName || !newPrinterUri || printersBusy">
                        <fa-icon [icon]="faPlus" class="me-1" /> Add
                      </button>
                    </div>
                  </form>
                  @if (printersLoading) {
                    <div class="text-center py-4"><span class="spinner-border spinner-border-sm"></span> Loading…</div>
                  } @else {
                    <table class="table table-sm table-hover align-middle mb-0">
                      <thead>
                        <tr>
                          <th>Name</th>
                          <th>Status</th>
                          <th>URI</th>
                          <th style="width:60px"></th>
                        </tr>
                      </thead>
                      <tbody>
                        @if (printers.length === 0) {
                          <tr><td colspan="4" class="text-center text-muted py-3">No printers configured.</td></tr>
                        }
                        @for (p of printers; track p.name) {
                          <tr>
                            <td class="small fw-semibold">{{ p.name }}</td>
                            <td class="small">{{ p.status }}</td>
                            <td class="small text-muted">{{ p.uri }}</td>
                            <td>
                              <button class="btn btn-sm btn-outline-danger" (click)="deletePrinter(p.name)" [disabled]="printersBusy">
                                <fa-icon [icon]="faTrashAlt" />
                              </button>
                            </td>
                          </tr>
                        }
                      </tbody>
                    </table>
                  }
                </div>
                <div class="modal-footer py-1">
                  <button class="btn btn-sm btn-secondary" (click)="showPrinters = false">Close</button>
                </div>
              </div>
            </div>
          </div>
        }

        <!-- Global Stats Modal -->
        @if (showAdminStatsModal) {
          <div class="modal fade show d-block" style="background:rgba(0,0,0,0.4)">
            <div class="modal-dialog">
              <div class="modal-content">
                <div class="modal-header py-2">
                  <h6 class="modal-title">Global Print Statistics</h6>
                  <button class="btn-close btn-close-sm" (click)="showAdminStatsModal = false"></button>
                </div>
                <div class="modal-body">
                  @if (adminStatsLoading) {
                    <div class="text-center py-4"><span class="spinner-border spinner-border-sm"></span> Loading…</div>
                  } @else if (adminStats) {
                    <div class="row g-3">
                      @for (bucket of adminStatsBuckets; track bucket.label) {
                        <div class="col-6">
                          <div class="card h-100">
                            <div class="card-body">
                              <div class="text-muted small text-uppercase">{{ bucket.label }}</div>
                              <div class="h4 mb-0">{{ bucket.pages }} <small class="text-muted">pages</small></div>
                              <div class="text-muted small">{{ bucket.jobs }} job(s)</div>
                            </div>
                          </div>
                        </div>
                      }
                    </div>
                  }
                </div>
                <div class="modal-footer py-1">
                  <button class="btn btn-sm btn-secondary" (click)="showAdminStatsModal = false">Close</button>
                </div>
              </div>
            </div>
          </div>
        }

        <!-- Global Log Modal (admin-group only) -->
        @if (showGlobalLog) {
          <div class="modal fade show d-block" style="background:rgba(0,0,0,0.4)">
            <div class="modal-dialog modal-xl">
              <div class="modal-content">
                <div class="modal-header py-2">
                  <h6 class="modal-title">Global Print Log</h6>
                  <button class="btn-close btn-close-sm" (click)="showGlobalLog = false"></button>
                </div>
                <div class="modal-body p-0">
                  @if (globalLogLoading) {
                    <div class="text-center py-4">
                      <span class="spinner-border spinner-border-sm"></span> Loading log…
                    </div>
                  } @else {
                    <div class="table-responsive" style="max-height: 70vh; overflow-y: auto;">
                      <table class="table table-sm table-hover align-middle mb-0">
                        <thead class="sticky-top bg-body">
                          <tr>
                            <th style="width: 100px">User</th>
                            <th style="min-width: 200px">Printer</th>
                            <th style="min-width: 150px">Document</th>
                            <th style="width: 160px">Date/Time</th>
                            <th style="width: 70px" class="text-end">Pages</th>
                            <th style="width: 100px">Size</th>
                          </tr>
                          <tr>
                            <td>
                              <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                                [(ngModel)]="globalLogFilterUser" (ngModelChange)="applyGlobalLogFilters()">
                            </td>
                            <td>
                              <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                                [(ngModel)]="globalLogFilterUrl" (ngModelChange)="applyGlobalLogFilters()">
                            </td>
                            <td>
                              <input type="text" class="form-control form-control-sm" placeholder="Filter…"
                                [(ngModel)]="globalLogFilterName" (ngModelChange)="applyGlobalLogFilters()">
                            </td>
                            <td></td>
                            <td></td>
                            <td></td>
                          </tr>
                        </thead>
                        <tbody>
                          @if (filteredGlobalLog.length === 0) {
                            <tr>
                              <td colspan="6" class="text-center text-muted py-3">
                                @if (globalLogEntries.length === 0) {
                                  No log entries.
                                } @else {
                                  No matching entries.
                                }
                              </td>
                            </tr>
                          }
                          @for (entry of filteredGlobalLog; track $index) {
                            <tr>
                              <td class="small fw-semibold">{{ entry.username }}</td>
                              <td class="small text-nowrap">{{ entry.url }}</td>
                              <td class="small">{{ entry.name }}</td>
                              <td class="small text-nowrap">{{ entry.datetime }}</td>
                              <td class="small text-end text-nowrap">{{ entry.pages || '' }}</td>
                              <td class="small text-nowrap">{{ entry.size }}</td>
                            </tr>
                          }
                        </tbody>
                      </table>
                    </div>
                    <div class="text-muted small mt-2 px-3 pb-2">
                      {{ filteredGlobalLog.length }} of {{ globalLogEntries.length }} entries
                    </div>
                  }
                </div>
                <div class="modal-footer py-1 d-flex justify-content-between">
                  <button class="btn btn-sm btn-danger" (click)="showClearGlobalLogConfirm = true"
                    [disabled]="globalLogLoading || clearingGlobalLog || globalLogEntries.length === 0">
                    <i class="fa-solid fa-trash-can me-1"></i>Clear Global Log
                  </button>
                  <button class="btn btn-sm btn-secondary" (click)="showGlobalLog = false">Close</button>
                </div>
                <!-- Clear Global Log Confirmation Popup -->
                @if (showClearGlobalLogConfirm) {
                  <div class="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center"
                       style="background:rgba(0,0,0,0.5); z-index: 10;">
                    <div class="card shadow" style="min-width: 340px; max-width: 440px;">
                      <div class="card-header py-2 d-flex justify-content-between align-items-center">
                        <span class="fw-semibold">Confirm Clear Global Log</span>
                        <button class="btn-close btn-close-sm" (click)="showClearGlobalLogConfirm = false"></button>
                      </div>
                      <div class="card-body text-center">
                        @if (clearingGlobalLog) {
                          <span class="spinner-border spinner-border-sm me-1"></span> Processing…
                        } @else {
                          <p class="mb-3">How would you like to clear the global log?</p>
                          <div class="d-flex flex-column gap-2">
                            <button class="btn btn-warning" (click)="doClearGlobalLog(true)">
                              <i class="fa-solid fa-box-archive me-1"></i>Archive &amp; Clear
                            </button>
                            <button class="btn btn-danger" (click)="doClearGlobalLog(false)">
                              <i class="fa-solid fa-trash-can me-1"></i>Clear
                            </button>
                            <button class="btn btn-secondary" (click)="showClearGlobalLogConfirm = false">
                              Cancel
                            </button>
                          </div>
                        }
                      </div>
                    </div>
                  </div>
                }
              </div>
            </div>
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    .admin-overlay {
      position: fixed;
      inset: 0;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding-top: 60px;
      background: rgba(0,0,0,0.5);
      z-index: 9998;
      overflow-y: auto;
    }
    .admin-panel {
      width: 100%;
      max-width: 960px;
      padding: 1.5rem;
      border-radius: 12px;
      background: var(--bs-body-bg);
      border: 1px solid var(--bs-border-color);
      box-shadow: 0 8px 32px rgba(0,0,0,0.2);
      margin-bottom: 2rem;
    }
  `]
})
export class AdminPanelComponent implements OnInit {
  auth = inject(AuthService);
  private http = inject(HttpClient);
  readonly close = output<void>();
  readonly smtpOpen = output<void>();

  faTrashAlt = faTrashAlt;
  faKey = faKey;
  faUserPlus = faUserPlus;
  faLock = faLock;
  faUnlock = faUnlock;
  faTimes = faTimes;
  faArrowLeft = faArrowLeft;
  faCog = faCog;
  faSave = faSave;
  faShieldAlt = faShieldAlt;
  faToggleOn = faToggleOn;
  faToggleOff = faToggleOff;
  faEnvelope = faEnvelope;
  faClipboardList = faClipboardList;
  faPrint = faPrint;
  faPlus = faPlus;
  faChartBar = faChartBar;

  users: UserRecord[] = [];
  filteredUsers: UserRecord[] = [];
  filterUsername = '';
  filterGroup = '';
  filterHomedir = '';
  filterEmail = '';
  filterCookies = '';
  filter2fa = '';
  filterChangePw = '';
  statusMsg = '';
  statusIsError = false;

  newUsername = '';
  newPassword = '';
  newGroup = 'users-group';

  // Global log state
  showGlobalLog = false;
  globalLogLoading = false;
  globalLogEntries: { url: string; name: string; datetime: string; size: string; filename: string; username: string; pages?: number; file_exists: boolean }[] = [];
  filteredGlobalLog: typeof this.globalLogEntries = [];
  globalLogFilterUser = '';
  globalLogFilterUrl = '';
  globalLogFilterName = '';
  showClearGlobalLogConfirm = false;
  clearingGlobalLog = false;

  // CUPS printers state
  showPrinters = false;
  printersLoading = false;
  printersBusy = false;
  printers: { name: string; status: string; uri: string }[] = [];
  newPrinterName = '';
  newPrinterUri = '';
  printersStatusMsg = '';
  printersStatusErr = false;

  // Admin stats state
  showAdminStatsModal = false;
  adminStatsLoading = false;
  adminStats: Record<string, { pages: number; jobs: number }> | null = null;
  get adminStatsBuckets() {
    if (!this.adminStats) return [];
    return [
      { label: 'Today', ...this.adminStats['today'] },
      { label: 'This month', ...this.adminStats['month'] },
      { label: 'This year', ...this.adminStats['year'] },
      { label: 'Overall', ...this.adminStats['overall'] },
    ];
  }

  resetTarget: UserRecord | null = null;
  resetNewPassword = '';

  settingsTarget: UserRecord | null = null;
  settingsEntries: SettingEntry[] = [];
  settingsLoading = false;
  settingsDirty = false;
  settingsStatus = '';
  settingsStatusErr = false;
  editUsername = '';
  editHomedir = '';
  editEmail = '';
  currentAdminUsername = '';

  ngOnInit() {
    this.currentAdminUsername = this.auth.username() || '';
    this.loadUsers();
  }

  loadUsers() {
    this.auth.listUsers().subscribe({
      next: (res: { users: UserRecord[] }) => {
        this.users = res.users || [];
        this.applyUserFilters();
      },
      error: () => this.showStatus('Failed to load users.', true),
    });
  }

  applyUserFilters() {
    let result = this.users;
    if (this.filterUsername) {
      result = this._regexFilter(result, this.filterUsername, u => u.username);
    }
    if (this.filterGroup) {
      result = this._regexFilter(result, this.filterGroup, u => u.group);
    }
    if (this.filterHomedir) {
      result = this._regexFilter(result, this.filterHomedir, u => u.homedir || '');
    }
    if (this.filterEmail) {
      result = this._regexFilter(result, this.filterEmail, u => u.email || '');
    }
    if (this.filterCookies) {
      result = this._regexFilter(result, this.filterCookies, u => u.has_cookies ? 'Yes' : 'No');
    }
    if (this.filter2fa) {
      result = this._regexFilter(result, this.filter2fa, u => u.totp_enabled ? 'On' : 'Off');
    }
    if (this.filterChangePw) {
      result = this._regexFilter(result, this.filterChangePw, u => u.must_change_password ? 'Yes' : 'No');
    }
    this.filteredUsers = result;
  }

  private _regexFilter<T>(items: T[], pattern: string, accessor: (item: T) => string): T[] {
    try {
      const re = new RegExp(pattern, 'i');
      return items.filter(item => re.test(accessor(item)));
    } catch {
      const lower = pattern.toLowerCase();
      return items.filter(item => accessor(item).toLowerCase().includes(lower));
    }
  }

  addUser() {
    this.auth.createUser(this.newUsername, this.newPassword, this.newGroup).subscribe({
      next: (res: { status: string; msg: string }) => {
        if (res.status === 'ok') {
          this.showStatus(`User "${this.newUsername}" created.`);
          this.newUsername = '';
          this.newPassword = '';
          this.newGroup = 'guest-group';
          this.loadUsers();
        } else {
          this.showStatus(res.msg, true);
        }
      },
      error: (err: { error?: { msg?: string } }) => this.showStatus(err?.error?.msg || 'Error creating user.', true),
    });
  }

  toggleEnabled(user: UserRecord) {
    const newState = !user.enabled;
    this.auth.modifyUser(user.username, { enabled: newState } as any).subscribe({
      next: () => {
        user.enabled = newState;
        this.showStatus(`User "${user.username}" ${newState ? 'enabled' : 'disabled'}.`);
      },
      error: (err: { error?: { msg?: string } }) => this.showStatus(err?.error?.msg || 'Error.', true),
    });
  }

  changeGroup(user: UserRecord, group: string) {
    this.auth.modifyUser(user.username, { group } as any).subscribe({
      next: () => {
        user.group = group;
        this.showStatus(`Group for "${user.username}" changed to ${group}.`);
      },
      error: (err: { error?: { msg?: string } }) => this.showStatus(err?.error?.msg || 'Error.', true),
    });
  }

  adminDisable2fa(user: UserRecord) {
    if (!confirm(`Disable 2FA for "${user.username}"? This will remove their TOTP settings and log them out from all devices.`)) return;
    this.auth.adminDisable2fa(user.username).subscribe({
      next: (res: { status: string; msg: string }) => {
        if (res.status === 'ok') {
          this.showStatus(res.msg);
          this.loadUsers();
        } else {
          this.showStatus(res.msg, true);
        }
      },
      error: (err: { error?: { msg?: string } }) => this.showStatus(err?.error?.msg || 'Error.', true),
    });
  }

  deleteUser(user: UserRecord) {
    if (!confirm(`Delete user "${user.username}"? This cannot be undone.`)) return;
    this.auth.deleteUser(user.username).subscribe({
      next: (res: { status: string; msg: string }) => {
        if (res.status === 'ok') {
          this.showStatus(`User "${user.username}" deleted.`);
          this.loadUsers();
        } else {
          this.showStatus(res.msg, true);
        }
      },
      error: (err: { error?: { msg?: string } }) => this.showStatus(err?.error?.msg || 'Error.', true),
    });
  }

  openResetPassword(user: UserRecord) {
    this.resetTarget = user;
    this.resetNewPassword = '';
  }

  doResetPassword() {
    if (!this.resetTarget) return;
    this.auth.resetUserPassword(this.resetTarget.username, this.resetNewPassword).subscribe({
      next: (res: { status: string; msg: string }) => {
        if (res.status === 'ok') {
          this.showStatus(`Password reset for "${this.resetTarget!.username}".`);
          this.resetTarget = null;
          this.loadUsers();
        } else {
          this.showStatus(res.msg, true);
        }
      },
      error: (err: { error?: { msg?: string } }) => this.showStatus(err?.error?.msg || 'Error.', true),
    });
  }

  // --- User Settings ---

  openSettings(user: UserRecord) {
    this.settingsTarget = user;
    this.editUsername = user.username;
    this.editHomedir = user.homedir || '';
    this.editEmail = user.email || '';
    this.settingsEntries = [];
    this.settingsDirty = false;
    this.settingsStatus = '';
    this.settingsLoading = true;
    this.auth.getUserSettings(user.username).subscribe({
      next: (res: { status: string; settings: Record<string, unknown>; locked_settings: string[]; user_overrides?: string[] }) => {
        this.settingsLoading = false;
        const settings = res.settings || {};
        const locked = res.locked_settings || [];
        const overrides = res.user_overrides || [];
        // Known configurable keys for the printers backend
        const allKeys = [
          'DEFAULT_THEME',
        ];
        // Include any extra keys already present in the settings
        for (const k of Object.keys(settings)) {
          if (!allKeys.includes(k)) allKeys.push(k);
        }
        this.settingsEntries = allKeys.map(key => ({
          key,
          value: settings[key] != null ? String(settings[key]) : '',
          locked: locked.includes(key),
          isOverride: overrides.includes(key),
        }));
      },
      error: () => {
        this.settingsLoading = false;
        this.settingsStatus = 'Failed to load settings.';
        this.settingsStatusErr = true;
      },
    });
  }

  onSettingChanged() {
    this.settingsDirty = true;
  }

  get needsRenameOrHomedir(): boolean {
    if (!this.settingsTarget) return false;
    const origUsername = this.settingsTarget.username;
    const origHomedir = this.settingsTarget.homedir || '';
    const newUsername = this.editUsername.trim();
    const newHomedir = this.editHomedir.trim();
    return (newUsername !== '' && newUsername !== origUsername) ||
           (newHomedir !== '' && newHomedir !== origHomedir);
  }

  saveSettings(moveData: boolean = false) {
    if (!this.settingsTarget) return;
    const originalUsername = this.settingsTarget.username;
    const originalHomedir = this.settingsTarget.homedir || '';

    // Check if username changed (rename)
    const needsRename = this.editUsername.trim() !== '' && this.editUsername.trim() !== originalUsername;
    // Check if homedir changed
    const needsHomedirChange = this.editHomedir.trim() !== '' && this.editHomedir.trim() !== originalHomedir && !needsRename;

    this.settingsLoading = true;

    const doSaveSettings = (effectiveUsername: string) => {
      const settings: Record<string, string> = {};
      const locked: string[] = [];
      for (const e of this.settingsEntries) {
        if (e.value !== '') {
          settings[e.key] = e.value;
        }
        if (e.locked) {
          locked.push(e.key);
        }
      }
      this.auth.setUserSettings(effectiveUsername, settings, locked).subscribe({
        next: () => {
          this.settingsLoading = false;
          this.settingsDirty = false;
          this.settingsStatus = 'Settings saved.';
          this.settingsStatusErr = false;
          this.loadUsers();
          setTimeout(() => { this.settingsStatus = ''; }, 3000);
        },
        error: (err: { error?: { msg?: string } }) => {
          this.settingsLoading = false;
          this.settingsStatus = err?.error?.msg || 'Error saving settings.';
          this.settingsStatusErr = true;
        },
      });
    };

    if (needsRename) {
      // Rename user (with or without file move)
      this.auth.renameUser(originalUsername, this.editUsername.trim(), moveData).subscribe({
        next: (res: { status: string; msg: string }) => {
          if (res.status === 'ok') {
            const newUsername = this.editUsername.trim();
            // After rename, also handle homedir change if different from default
            const defaultHomedir = '/downloads/' + newUsername;
            if (this.editHomedir.trim() !== '' && this.editHomedir.trim() !== defaultHomedir
                && this.editHomedir.trim() !== originalHomedir) {
              this.auth.changeUserHomedir(newUsername, this.editHomedir.trim(), moveData).subscribe({
                next: (hdRes: { status: string; msg: string }) => {
                  if (hdRes.status !== 'ok') {
                    this.settingsStatus = hdRes.msg;
                    this.settingsStatusErr = true;
                  }
                  doSaveSettings(newUsername);
                },
                error: (err: { error?: { msg?: string } }) => {
                  this.settingsStatus = err?.error?.msg || 'Error changing home directory after rename.';
                  this.settingsStatusErr = true;
                  doSaveSettings(newUsername);
                },
              });
            } else {
              doSaveSettings(newUsername);
            }
          } else {
            this.settingsLoading = false;
            this.settingsStatus = res.msg;
            this.settingsStatusErr = true;
          }
        },
        error: (err: { error?: { msg?: string } }) => {
          this.settingsLoading = false;
          this.settingsStatus = err?.error?.msg || 'Error renaming user.';
          this.settingsStatusErr = true;
        },
      });
    } else if (needsHomedirChange) {
      this.auth.changeUserHomedir(originalUsername, this.editHomedir.trim(), moveData).subscribe({
        next: (res: { status: string; msg: string }) => {
          if (res.status === 'ok') {
            doSaveSettings(originalUsername);
          } else {
            this.settingsLoading = false;
            this.settingsStatus = res.msg;
            this.settingsStatusErr = true;
          }
        },
        error: (err: { error?: { msg?: string } }) => {
          this.settingsLoading = false;
          this.settingsStatus = err?.error?.msg || 'Error changing home directory.';
          this.settingsStatusErr = true;
        },
      });
    } else {
      doSaveSettings(originalUsername);
    }
  }

  adminSetEmail() {
    if (!this.settingsTarget || !this.editEmail.trim()) return;
    this.auth.adminSetUserEmail(this.settingsTarget.username, this.editEmail.trim()).subscribe({
      next: (res: { status: string; msg: string }) => {
        if (res.status === 'ok') {
          this.settingsTarget!.email = this.editEmail.trim();
          this.settingsTarget!.email_status = this.settingsTarget!.username === 'admin' ? 'verified' : 'pending';
          this.settingsStatus = res.msg;
          this.settingsStatusErr = false;
          this.loadUsers();
          setTimeout(() => { this.settingsStatus = ''; }, 3000);
        } else {
          this.settingsStatus = res.msg;
          this.settingsStatusErr = true;
        }
      },
      error: (err: { error?: { msg?: string } }) => {
        this.settingsStatus = err?.error?.msg || 'Error setting email.';
        this.settingsStatusErr = true;
      },
    });
  }

  adminDeleteEmail() {
    if (!this.settingsTarget) return;
    if (!confirm(`Remove email for "${this.settingsTarget.username}"?`)) return;
    this.auth.adminDeleteUserEmail(this.settingsTarget.username).subscribe({
      next: (res: { status: string; msg: string }) => {
        if (res.status === 'ok') {
          this.settingsTarget!.email = '';
          this.settingsTarget!.email_status = 'none';
          this.editEmail = '';
          this.settingsStatus = res.msg;
          this.settingsStatusErr = false;
          this.loadUsers();
          setTimeout(() => { this.settingsStatus = ''; }, 3000);
        } else {
          this.settingsStatus = res.msg;
          this.settingsStatusErr = true;
        }
      },
      error: (err: { error?: { msg?: string } }) => {
        this.settingsStatus = err?.error?.msg || 'Error deleting email.';
        this.settingsStatusErr = true;
      },
    });
  }

  changeGroupInSettings(newGroup: string) {
    if (!this.settingsTarget) return;
    this.auth.modifyUser(this.settingsTarget.username, { group: newGroup } as any).subscribe({
      next: () => {
        this.settingsTarget!.group = newGroup;
        this.settingsStatus = `Group changed to ${newGroup}.`;
        this.settingsStatusErr = false;
        this.loadUsers();
        setTimeout(() => { this.settingsStatus = ''; }, 3000);
      },
      error: (err: { error?: { msg?: string } }) => {
        this.settingsStatus = err?.error?.msg || 'Error changing group.';
        this.settingsStatusErr = true;
      },
    });
  }

  private showStatus(msg: string, isError = false) {
    this.statusMsg = msg;
    this.statusIsError = isError;
    setTimeout(() => { this.statusMsg = ''; }, 4000);
  }

  /** Check if current user can manage the target user. */
  canManage(user: UserRecord): boolean {
    if (user.username === 'admin') return false;
    const myGroup = this.auth.group();
    if (myGroup === 'admin-group') return true;
    if (myGroup === 'useradmin-group') {
      return user.group === 'users-group' || user.group === 'useradmin-group';
    }
    return false;
  }

  openSmtp() {
    this.smtpOpen.emit();
  }

  openPrinters() {
    this.showPrinters = true;
    this.printersStatusMsg = '';
    this.newPrinterName = '';
    this.newPrinterUri = '';
    this.loadPrinters();
  }

  loadPrinters() {
    this.printersLoading = true;
    this.http.get<{ printers: { name: string; status: string; uri: string }[] }>('api/admin/printers').subscribe({
      next: (res) => { this.printersLoading = false; this.printers = res.printers || []; },
      error: () => { this.printersLoading = false; this.showPrintersStatus('Failed to load printers.', true); },
    });
  }

  addPrinter() {
    if (!this.newPrinterName || !this.newPrinterUri) return;
    this.printersBusy = true;
    this.http.post<{ status: string; msg: string }>('api/admin/printers', {
      name: this.newPrinterName, uri: this.newPrinterUri
    }).subscribe({
      next: (res) => {
        this.printersBusy = false;
        if (res.status === 'ok') {
          this.newPrinterName = ''; this.newPrinterUri = '';
          this.showPrintersStatus('Printer added.');
          this.loadPrinters();
        } else {
          this.showPrintersStatus(res.msg, true);
        }
      },
      error: (err: any) => { this.printersBusy = false; this.showPrintersStatus(err?.error?.msg || 'Error.', true); },
    });
  }

  deletePrinter(name: string) {
    if (!confirm(`Remove printer "${name}" from CUPS?`)) return;
    this.printersBusy = true;
    this.http.delete<{ status: string; msg: string }>(`api/admin/printers/${encodeURIComponent(name)}`).subscribe({
      next: (res) => {
        this.printersBusy = false;
        if (res.status === 'ok') { this.showPrintersStatus('Printer removed.'); this.loadPrinters(); }
        else { this.showPrintersStatus(res.msg, true); }
      },
      error: (err: any) => { this.printersBusy = false; this.showPrintersStatus(err?.error?.msg || 'Error.', true); },
    });
  }

  private showPrintersStatus(msg: string, isError = false) {
    this.printersStatusMsg = msg; this.printersStatusErr = isError;
    setTimeout(() => { this.printersStatusMsg = ''; }, 4000);
  }

  openAdminStats() {
    this.showAdminStatsModal = true;
    this.adminStatsLoading = true;
    this.http.get<Record<string, { pages: number; jobs: number }>>('api/admin/stats').subscribe({
      next: (res) => { this.adminStatsLoading = false; this.adminStats = res; },
      error: () => { this.adminStatsLoading = false; this.adminStats = null; },
    });
  }

  openGlobalLog() {
    this.showGlobalLog = true;
    this.globalLogLoading = true;
    this.globalLogFilterUser = '';
    this.globalLogFilterUrl = '';
    this.globalLogFilterName = '';
    this.auth.getGlobalLog().subscribe({
      next: (res) => {
        this.globalLogLoading = false;
        this.globalLogEntries = res.entries || [];
        this.filteredGlobalLog = this.globalLogEntries;
      },
      error: () => {
        this.globalLogLoading = false;
        this.globalLogEntries = [];
        this.filteredGlobalLog = [];
      },
    });
  }

  doClearGlobalLog(archive: boolean) {
    this.clearingGlobalLog = true;
    this.auth.clearGlobalLog(archive).subscribe({
      next: (res) => {
        this.clearingGlobalLog = false;
        this.showClearGlobalLogConfirm = false;
        if (res.status === 'ok') {
          this.globalLogEntries = [];
          this.filteredGlobalLog = [];
          this.showStatus(archive ? `Global log archived and cleared (${res.deleted} file(s)).` : `Global log cleared (${res.deleted} file(s)).`);
        } else {
          this.showStatus(res.msg || 'Failed to clear global log.', true);
        }
      },
      error: (err: { error?: { msg?: string } }) => {
        this.clearingGlobalLog = false;
        this.showClearGlobalLogConfirm = false;
        this.showStatus(err?.error?.msg || 'Error clearing global log.', true);
      },
    });
  }

  applyGlobalLogFilters() {
    let result = this.globalLogEntries;
    if (this.globalLogFilterUser) {
      result = this._regexFilter(result, this.globalLogFilterUser, e => e.username);
    }
    if (this.globalLogFilterUrl) {
      result = this._regexFilter(result, this.globalLogFilterUrl, e => e.url);
    }
    if (this.globalLogFilterName) {
      result = this._regexFilter(result, this.globalLogFilterName, e => e.name);
    }
    this.filteredGlobalLog = result;
  }
}
