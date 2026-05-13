import { Pipe, PipeTransform } from "@angular/core";

@Pipe({
    name: 'speed',
})
export class SpeedPipe implements PipeTransform {
  transform(value: number): string {
    if (value === null || value === undefined || isNaN(value) || value <= 0) {
      return '';
    }

    const k = 1024;
    const dm = 2;
    const sizes = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s', 'PB/s', 'EB/s', 'ZB/s', 'YB/s'];
    const i = Math.floor(Math.log(value) / Math.log(k));
    return parseFloat((value / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
  }
}