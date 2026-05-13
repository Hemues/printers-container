
export interface Download {
  id: string;
  title: string;
  url: string;
  quality: string;
  format: string;
  folder: string;
  custom_name_prefix: string;
  playlist_item_limit: number;
  status: string;
  msg: string;
  percent: number;
  speed: number;
  eta: number;
  filename: string;
  checked: boolean;
  size?: number;
  error?: string;
  deleting?: boolean;
  timestamp?: number;
  _done_key?: string;
}
