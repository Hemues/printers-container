import { Injectable, inject } from '@angular/core';
import { ApplicationRef } from '@angular/core';
import { Socket } from 'ngx-socket-io';

@Injectable(
  { providedIn: 'root' }
)
export class PrintersSocket extends Socket {

  constructor() {
    const appRef = inject(ApplicationRef);

    const path =
      document.location.pathname.replace(/share-target/, '') + 'socket.io';
    const token = localStorage.getItem('Printers_token') || '';
    super({ url: '', options: { path, query: { token } } }, appRef);
  }

  /** Reconnect with a fresh token (called after login). */
  reconnectWithToken() {
    const token = localStorage.getItem('Printers_token') || '';
    (this.ioSocket as any).io.opts.query = { token };
    this.ioSocket.disconnect();
    this.ioSocket.connect();
  }
}
