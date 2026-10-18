import type { Dispatch, SetStateAction } from "react";
export type Key = {
  id: string;
  name: string;
  prefix?: string;
  revoked?: boolean;
  usage: { total: number; requests_24h: number; errors_24h: number };
};
export type Task = {
  id: string;
  status: string;
  kind: string;
  created_at: number;
  result: unknown;
  error: unknown;
};
export type RequestRow = {
  id: number;
  ts: number;
  method: string;
  path: string;
  model: string;
  account_id: string;
  status_code: number;
  latency_ms: number;
  error?: string;
  task_status?: string;
  key_name?: string;
};
export type Status = {
  version: string;
  driver: { driver: string; ok: boolean; tabs?: unknown };
  accounts: Record<string, number>;
};
export type Pool = {
  strategy: string;
  max_concurrency: number;
  cooldown: number;
  acquire_timeout: number;
};

export type Setter<T> = Dispatch<SetStateAction<T>>;
export type Act = (
  action: () => Promise<void>,
  message?: string,
) => Promise<void>;
export type Ask = (text: string, action: () => Promise<unknown>) => void;
export type Detail = { title: string; body: unknown };
export type ModalKind = "import" | "login" | "key" | null;
