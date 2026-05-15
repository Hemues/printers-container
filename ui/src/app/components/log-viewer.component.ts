import { Component, inject, OnInit, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { FontAwesomeModule } from '@fortawesome/angular-fontawesome';
import { faXmark, faArrowLeft, faTrashAlt, faSearch, faDownload, faFileImport } from '@fortawesome/free-solid-svg-icons';
import { AuthService } from '../services/auth.service';
import { DownloadsService } from '../services/downloads.service';

interface LogEntry {
  url: string;        // printer name
  name: string;       // document title
  datetime: string;
  size: string;
  filename: string;   // basename of captured PDF
  username: string;   // owner of the job
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
        <div class="log-header d-flex justify-content-between align-items-center mb-3">
          <h4 class="mb-0">
            <button class="btn btn-sm btn-outline-secondary me-2" (click)="close.emit()">
              <fa-icon [icon]="faArrowLeft" />
            </button>
            Print Log
          </h4>
          <div class="d-flex gap-2">
            <button class="btn btn-sm btn-outline-primary" (click)="downloadSelected()"
              [disabled]="selectedCount === 0">
              <fa-icon [icon]="faDownload" class="me-1" /> Download Selected
            </button>
            <button class="btn btn-sm btn-outline-danger" (click)="deleteSelected()"
              [disabled]="selectedCount === 0">
              <fa-icon [icon]="faTrashAlt" class="me-1" /> Delete Selected
            </button>
            <button class="btn btn-sm btn-outline-info" (click)="recoverLog()"
              [disabled]="loading">
              <fa-icon [icon]="faFileImport" class="me-1" /> Detect Existing Files
            </button>
            <button class="btn btn-sm btn-outline-danger" (click)="clearLog()"
              [disabled]="entries.length === 0 || loading">
              <fa-icon [icon]="faTrashAlt" class="me-1" /> Clear Log
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
            <span class="spinner-border spinner-border-sm"></span> Loading log…
          </div>
        } @else {
          <!-- Search filters -->
          <div class="table-responsive" style="max-height: 70vh; overflow-y: auto;">
            <table class="table table-sm table-hover align-middle mb-0">
              <thead class="sticky-top bg-body">
                <tr>
                  <th style="width: 1.5rem;">
                    <div class="form-check">
                      <input type="checkbox" class="form-check-input"
                        [checked]="masterChecked"
                        [indeterminate]="masterIndeterminate"
                        (change)="toggleSelectAll($event)">
                    </div>
                  </th>
                  <th style="min-width: 200px">
                    <div class="d-flex align-items-center gap-1">
                      <fa-icon [icon]="faSearch" class="text-muted" style="font-size:0.75em" />
                      <input type="text" class="form-control form-control-sm"
                        placeholder="Filter printer (regex)"
                        [(ngModel)]="filterUrl"
                        (ngModelChange)="applyFilters()">
                    </div>
                  </th>
                  <th style="min-width: 150px">
                    <div class="d-flex align-items-center gap-1">
                      <fa-icon [icon]="faSearch" class="text-muted" style="font-size:0.75em" />
                      <input type="text" class="form-control form-control-sm"
                        placeholder="Filter document (regex)"
                        [(ngModel)]="filterName"
                        (ngModelChange)="applyFilters()">
                    </div>
                  </th>
                  <th style="width: 160px">Date/Time</th>
                  <th style="width: 70px">Pages</th>
                  <th style="width: 100px">Size</th>
                  <th style="width: 80px"></th>
                </tr>
              </thead>
              <tbody>
                @if (filteredEntries.length === 0) {
                  <tr>
                    <td colspan="7" class="text-center text-muted py-3">
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
                        <div class="form-check">
                          <input type="checkbox" class="form-check-input"
                            [(ngModel)]="entry.checked"
                            (ngModelChange)="updateSelection()">
                        </div>
                      }
                    </td>
                    <td class="small text-break" style="max-width: 300px; overflow: hidden; text-overflow: ellipsis;">
                      {{ entry.url }}
                    </td>
                    <td class="small">
                      @if (entry.file_exists && entry.filename) {
                        <a [href]="buildFileLink(entry)" target="_blank" class="text-decoration-none">{{ entry.name }}</a>
                      } @else {
                        {{ entry.name }}
                      }
                    </td>
                    <td class="small text-nowrap">{{ entry.datetime }}</td>
                    <td class="small text-nowrap text-end">{{ entry.pages || '' }}</td>
                    <td class="small text-nowrap">{{ entry.size }}</td>
                    <td>
                      @if (entry.file_exists && entry.filename) {
                        <div class="d-flex">
                          <a [href]="buildFileLink(entry)" download class="btn btn-link btn-sm p-0 me-2">
                            <fa-icon [icon]="faDownload" />
                          </a>
                          <button class="btn btn-link btn-sm p-0 text-danger" (click)="deleteFile(entry)"
                            title="Delete file from disk">
                            <fa-icon [icon]="faTrashAlt" />
                          </button>
                        </div>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          <div class="text-muted small mt-2 px-1">
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
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding-top: 60px;
      background: rgba(0,0,0,0.5);
      z-index: 9999;
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
      margin-bottom: 2rem;
    }
  `]
})
export class LogViewerComponent implements OnInit {
  private auth = inject(AuthService);
  private downloads = inject(DownloadsService);
  readonly close = output<void>();

  faXmark = faXmark;
  faArrowLeft = faArrowLeft;
  faTrashAlt = faTrashAlt;
  faSearch = faSearch;
  faDownload = faDownload;
  faFileImport = faFileImport;

  entries: LogEntry[] = [];
  filteredEntries: LogEntry[] = [];
  loading = true;
  statusMsg = '';
  statusIsError = false;

  filterUrl = '';
  filterName = '';

  // Selection state
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
    if (this.filterUrl) {
      try {
        const re = new RegExp(this.filterUrl, 'i');
        result = result.filter(e => re.test(e.url));
      } catch {
        const lower = this.filterUrl.toLowerCase();
        result = result.filter(e => e.url.toLowerCase().includes(lower));
      }
    }
    if (this.filterName) {
      try {
        const re = new RegExp(this.filterName, 'i');
        result = result.filter(e => re.test(e.name));
      } catch {
        const lower = this.filterName.toLowerCase();
        result = result.filter(e => e.name.toLowerCase().includes(lower));
      }
    }
    this.filteredEntries = result;
    this.updateSelection();
  }

  // --- Selection ---

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

  downloadSelected() {
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

  deleteSelected() {
    const selected = this.getSelectedEntries();
    if (selected.length === 0) return;
    if (!confirm(`Delete ${selected.length} file(s) from disk and remove from log? This cannot be undone.`)) return;
    const filenames = selected.map(e => e.filename);
    this.auth.deleteLogFiles(filenames, true).subscribe({
      next: (res) => {
        const deletedSet = new Set(res.deleted || []);
        // Remove deleted entries from the arrays
        this.entries = this.entries.filter(e => !deletedSet.has(e.filename));
        this.applyFilters();
        const count = deletedSet.size;
        if (res.errors?.length) {
          this.showStatus(`Deleted ${count} file(s). ${res.errors.length} error(s).`, res.errors.length > 0);
        } else {
          this.showStatus(`Deleted ${count} file(s) and removed from log.`);
        }
      },
      error: (err) => this.showStatus(err?.error?.msg || 'Failed to delete files.', true),
    });
  }

  // --- Single file actions ---

  deleteFile(entry: LogEntry) {
    if (!confirm(`Delete file "${entry.name}" from disk? This cannot be undone.`)) return;
    this.auth.deleteLogFiles([entry.filename]).subscribe({
      next: (res) => {
        if ((res.deleted || []).length > 0) {
          entry.file_exists = false;
          entry.checked = false;
          this.updateSelection();
          this.showStatus('File deleted.');
        } else {
          this.showStatus(res.errors?.[0] || 'Failed to delete file.', true);
        }
      },
      error: (err) => this.showStatus(err?.error?.msg || 'Failed to delete file.', true),
    });
  }

  recoverLog() {
    this.loading = true;
    this.auth.recoverDownloadLog().subscribe({
      next: (res) => {
        if (res.recovered > 0) {
          this.showStatus(`Recovered ${res.recovered} log entries.`);
          this.loadLog();
        } else {
          this.loading = false;
          this.showStatus('No recoverable entries found.');
        }
      },
      error: () => {
        this.loading = false;
        this.showStatus('Failed to recover log entries.', true);
      },
    });
  }

  clearLog() {
    if (!confirm('Clear all log entries? This cannot be undone.')) return;
    this.auth.clearDownloadLog().subscribe({
      next: () => {
        this.entries = [];
        this.filteredEntries = [];
        this.updateSelection();
        this.showStatus('Log cleared.');
      },
      error: () => this.showStatus('Failed to clear log.', true),
    });
  }

  private showStatus(msg: string, isError = false) {
    this.statusMsg = msg;
    this.statusIsError = isError;
    setTimeout(() => { this.statusMsg = ''; }, 4000);
  }
}
