import { Component, inject, OnInit, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { FontAwesomeModule } from '@fortawesome/angular-fontawesome';
import { faXmark, faSearch, faDownload } from '@fortawesome/free-solid-svg-icons';
import { AuthService } from '../services/auth.service';
import { DownloadsService } from '../services/downloads.service';

interface LogEntry {
  url: string;
  name: string;
  datetime: string;
  size: string;
  filename: string;
  username: string;
  pages: number;
  status: string;
  file_exists: boolean;
  checked: boolean;
}

@Component({
  selector: 'app-log-viewer',
  standalone: true,
  imports: [FormsModule, FontAwesomeModule],
  template: `
    <div class="log-overlay">
      <div class="log-panel">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <h5 class="mb-0">Print Log</h5>
          <div class="d-flex gap-2">
            <button class="btn btn-sm btn-outline-primary" (click)="exportSelected()"
              [disabled]="selectedCount === 0">
              <fa-icon [icon]="faDownload" class="me-1" /> Export Selected
            </button>
            <button class="btn btn-sm btn-outline-secondary" (click)="close.emit()">
              <fa-icon [icon]="faXmark" />
            </button>
          </div>
        </div>

        @if (statusMsg) {
          <div class="alert py-2" [class.alert-success]="!statusIsError" [class.alert-danger]="statusIsError">
            {{ statusMsg }}
          </div>
        }

        @if (loading) {
          <div class="text-center py-4">
            <span class="spinner-border spinner-border-sm"></span> Loading...
          </div>
        } @else {
          <div class="table-responsive">
            <table class="table table-sm table-hover align-middle mb-0">
              <thead class="sticky-top bg-body">
                <tr>
                  <th style="width: 1.5rem;">
                    <div class="form-check mb-0">
                      <input type="checkbox" class="form-check-input"
                        [checked]="masterChecked"
                        [indeterminate]="masterIndeterminate"
                        (change)="toggleSelectAll($event)">
                    </div>
                  </th>
                  <th>
                    <div class="d-flex align-items-center gap-1">
                      <fa-icon [icon]="faSearch" class="text-muted" style="font-size:0.75em" />
                      <input type="text" class="form-control form-control-sm"
                        placeholder="Filename"
                        [(ngModel)]="filterName"
                        (ngModelChange)="applyFilters()">
                    </div>
                  </th>
                  <th style="width: 160px;">
                    <div class="d-flex align-items-center gap-1">
                      <fa-icon [icon]="faSearch" class="text-muted" style="font-size:0.75em" />
                      <input type="text" class="form-control form-control-sm"
                        placeholder="Date"
                        [(ngModel)]="filterDate"
                        (ngModelChange)="applyFilters()">
                    </div>
                  </th>
                  <th style="width: 130px;">
                    <div class="d-flex align-items-center gap-1">
                      <fa-icon [icon]="faSearch" class="text-muted" style="font-size:0.75em" />
                      <input type="text" class="form-control form-control-sm"
                        placeholder="Status"
                        [(ngModel)]="filterStatus"
                        (ngModelChange)="applyFilters()">
                    </div>
                  </th>
                </tr>
              </thead>
              <tbody>
                @if (filteredEntries.length === 0) {
                  <tr>
                    <td colspan="4" class="text-center text-muted py-3">
                      @if (entries.length === 0) {
                        No log entries.
                      } @else {
                        No matching entries.
                      }
                    </td>
                  </tr>
                }
                @for (entry of filteredEntries; track $index) {
                  <tr>
                    <td>
                      @if (entry.file_exists && entry.filename) {
                        <div class="form-check mb-0">
                          <input type="checkbox" class="form-check-input"
                            [(ngModel)]="entry.checked"
                            (ngModelChange)="updateSelection()">
                        </div>
                      }
                    </td>
                    <td class="small text-break">
                      @if (entry.file_exists && entry.filename) {
                        <a [href]="buildFileLink(entry)" target="_blank" class="text-decoration-none">{{ entry.filename }}</a>
                      } @else {
                        {{ entry.filename || entry.name }}
                      }
                    </td>
                    <td class="small text-nowrap">{{ entry.datetime }}</td>
                    <td>
                      <span class="badge"
                        [class.bg-success]="entry.status === 'finished'"
                        [class.bg-danger]="entry.status === 'failed'"
                        [class.bg-secondary]="entry.status !== 'finished' && entry.status !== 'failed'">
                        {{ entry.status }}
                      </span>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <div class="text-muted small mt-2">
            {{ filteredEntries.length }} of {{ entries.length }} entries
            @if (selectedCount > 0) {
              &nbsp;· {{ selectedCount }} selected
            }
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    .log-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.5);
      z-index: 9999;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 1rem;
      overflow-y: auto;
    }
    .log-panel {
      width: 100%;
      max-width: 1100px;
      padding: 1.5rem;
      border-radius: 12px;
      background: var(--bs-body-bg);
      border: 1px solid var(--bs-border-color);
      box-shadow: 0 8px 32px rgba(0,0,0,0.2);
      margin-top: 4rem;
      margin-bottom: 2rem;
    }
  `]
})
export class LogViewerComponent implements OnInit {
  private auth = inject(AuthService);
  private downloads = inject(DownloadsService);
  readonly close = output<void>();

  faXmark = faXmark;
  faSearch = faSearch;
  faDownload = faDownload;

  entries: LogEntry[] = [];
  filteredEntries: LogEntry[] = [];
  loading = true;
  statusMsg = '';
  statusIsError = false;

  filterName = '';
  filterDate = '';
  filterStatus = '';

  masterChecked = false;
  masterIndeterminate = false;
  selectedCount = 0;

  ngOnInit() {
    this.loadLog();
  }

  loadLog() {
    this.loading = true;
    this.auth.getDownloadLog().subscribe({
      next: (res) => {
        this.loading = false;
        this.entries = (res.entries || []).map((e: any) => this.parseEntry(e)).reverse();
        this.applyFilters();
        this.updateSelection();
      },
      error: () => {
        this.loading = false;
        this.showStatus('Failed to load log.', true);
      },
    });
  }

  parseEntry(entry: any): LogEntry {
    if (typeof entry === 'object' && entry !== null) {
      return {
        url: entry.url || entry.printer || '',
        name: entry.name || entry.title || '',
        datetime: entry.datetime || '',
        size: entry.size || '',
        filename: entry.filename || '',
        username: entry.username || '',
        pages: Number(entry.pages || 0),
        status: entry.status || '',
        file_exists: entry.file_exists !== false,
        checked: false,
      };
    }
    return { url: String(entry), name: '', datetime: '', size: '', filename: '', username: '', pages: 0, status: '', file_exists: false, checked: false };
  }

  buildFileLink(entry: LogEntry): string {
    const baseDir = this.downloads.configuration['PUBLIC_HOST_URL'] || 'printings/';
    const segments = [entry.username, entry.filename].filter(s => !!s);
    const encoded = segments.map(p => encodeURIComponent(p)).join('/');
    return baseDir + encoded;
  }

  applyFilters() {
    let result = this.entries;
    if (this.filterName) {
      const lower = this.filterName.toLowerCase();
      result = result.filter(e => (e.filename || e.name).toLowerCase().includes(lower));
    }
    if (this.filterDate) {
      const lower = this.filterDate.toLowerCase();
      result = result.filter(e => e.datetime.toLowerCase().includes(lower));
    }
    if (this.filterStatus) {
      const lower = this.filterStatus.toLowerCase();
      result = result.filter(e => e.status.toLowerCase().includes(lower));
    }
    this.filteredEntries = result;
    this.updateSelection();
  }

  toggleSelectAll(event: Event) {
    const checked = (event.target as HTMLInputElement).checked;
    for (const entry of this.filteredEntries) {
      if (entry.file_exists && entry.filename) {
        entry.checked = checked;
      }
    }
    this.updateSelection();
  }

  updateSelection() {
    const checkable = this.filteredEntries.filter(e => e.file_exists && e.filename);
    const checked = checkable.filter(e => e.checked).length;
    this.selectedCount = checked;
    this.masterChecked = checkable.length > 0 && checked === checkable.length;
    this.masterIndeterminate = checked > 0 && checked < checkable.length;
  }

  private getSelectedEntries(): LogEntry[] {
    return this.filteredEntries.filter(e => e.checked && e.file_exists && e.filename);
  }

  exportSelected() {
    const selected = this.getSelectedEntries();
    for (const entry of selected) {
      const link = document.createElement('a');
      link.href = this.buildFileLink(entry);
      link.setAttribute('download', entry.filename.split('/').pop() || entry.name);
      link.setAttribute('target', '_self');
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }
  }

  private showStatus(msg: string, isError = false) {
    this.statusMsg = msg;
    this.statusIsError = isError;
    setTimeout(() => { this.statusMsg = ''; }, 4000);
  }
}