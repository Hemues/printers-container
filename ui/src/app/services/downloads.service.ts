import { inject, Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { of, Subject } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { PrintersSocket } from './printers-socket.service';
import { Download, State } from '../interfaces';

/**
 * Print job service.
 *
 * The class name is kept as ``DownloadsService`` for API parity with the
 * components forked from videodl-container (log-viewer in particular).
 * It maps Socket.IO ``added`` / ``completed`` events from the printers
 * backend into the same Download-shaped objects the UI already renders.
 */
@Injectable({ providedIn: 'root' })
export class DownloadsService {
  private http = inject(HttpClient);
  private socket = inject(PrintersSocket);

  loading = true;
  // queue is always empty for printers (jobs complete instantly on capture)
  queue = new Map<string, Download>();
  done = new Map<string, Download>();
  queueChanged = new Subject<unknown>();
  doneChanged = new Subject<unknown>();
  downloadCompleted = new Subject<Download>();
  customDirsChanged = new Subject<unknown>();
  configurationChanged = new Subject<unknown>();
  updated = new Subject<unknown>();
  addError = new Subject<{ url: string; msg: string }>();

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  configuration: any = { PUBLIC_HOST_URL: 'printings/' };
  customDirs = {};

  constructor() {
    this.socket.fromEvent('all')
      .pipe(takeUntilDestroyed())
      .subscribe((raw: string) => {
        this.loading = false;
        try {
          const list = JSON.parse(raw) as Download[];
          this.done.clear();
          for (const j of list) {
            const key = this.keyFor(j);
            this.done.set(key, this.toDownload(j as unknown as Partial<Download> & Record<string, unknown>));
          }
          this.doneChanged.next(null);
        } catch { /* ignore */ }
      });

    this.socket.fromEvent('added')
      .pipe(takeUntilDestroyed())
      .subscribe((raw: string) => this.upsert(raw));

    this.socket.fromEvent('completed')
      .pipe(takeUntilDestroyed())
      .subscribe((raw: string) => this.upsert(raw));
  }

  private keyFor(job: Partial<Download> & { id?: string; filename?: string }): string {
    return job.id || job.filename || (job.url ?? '') + ':' + (job.title ?? '');
  }

  private toDownload(job: Partial<Download> & Record<string, unknown>): Download {
    return {
      ...(job as Download),
      url: (job['printer'] as string) || (job.url ?? ''),
      title: (job['title'] as string) || ((job['name'] as string) ?? ''),
      checked: false,
      deleting: false,
    } as Download;
  }

  private upsert(raw: string) {
    try {
      const job = JSON.parse(raw) as Download & Record<string, unknown>;
      const key = this.keyFor(job);
      this.done.set(key, this.toDownload(job));
      this.doneChanged.next(null);
      this.downloadCompleted.next(this.done.get(key)!);
    } catch { /* ignore */ }
  }

  handleHTTPError(error: HttpErrorResponse) {
    const msg = error.error instanceof ErrorEvent ? error.error.message : error.error;
    return of({ status: 'error', msg });
  }

  /* ------------------------------------------------------------------
   * No-op shims kept so legacy templates / components still compile.
   * ------------------------------------------------------------------ */
  public add() { return of({ status: 'error', msg: 'Not applicable for the print server.' }); }
  public startById(_ids: string[]) { return of({ status: 'ok' }); }
  public delById(where: State, ids: string[]) {
    const map = this[where];
    if (map) for (const id of ids) map.delete(id);
    if (where === 'done') this.doneChanged.next(null);
    return of({ status: 'ok' });
  }
  public startByFilter() { return of({ status: 'ok' }); }
  public delByFilter() { return of({ status: 'ok' }); }
  public pauseAll() { return of({ status: 'ok' }); }
  public resumeAll() { return of({ status: 'ok' }); }
  public stopAll() { return of({ status: 'ok' }); }
  public startAll() { return of({ status: 'ok' }); }
  public updateMaxConcurrent() { return of({ status: 'ok' }); }
  public updateConfig() { return of({ status: 'ok' }); }
  public addDownloadByUrl() { return Promise.resolve({ status: 'error', msg: 'Not applicable.' }); }
  public exportQueueUrls(): string[] { return []; }
}
