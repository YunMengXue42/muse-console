import type { Dispatch, SetStateAction } from "react";
export type Key = {
  id: string;
  name: string;
  prefix?: string;
  note?: string;
  revoked?: boolean;
  created_at?: number;
  last_used_at?: number | null;
  usage: {
    total: number;
    requests_24h: number;
    errors_24h: number;
    last_request_at?: number | null;
  };
};
export type Task = {
  id: string;
  status: string;
  kind: string;
  progress?: number;
  owner_key_id?: string | null;
  created_at: number;
  updated_at?: number;
  request?: {
    prompt?: string;
    model?: string | null;
    size?: string | null;
    duration?: number | null;
    seconds?: number | null;
    n?: number;
  };
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
  task_id?: string | null;
  task_ms?: number | null;
  key_id?: string | null;
  key_name?: string;
  stream?: boolean;
  poll?: boolean;
  client_ip?: string | null;
};
export type Status = {
  version: string;
  driver: { driver: string; ok: boolean; tabs?: unknown };
  capabilities?: {
    chat?: boolean;
    images?: boolean;
    videos?: boolean;
    login_browser?: boolean;
    renew_session?: boolean;
    quota?: boolean;
  };
  accounts: Record<string, number>;
  tasks?: { pending: number; running: number; max_pending: number };
};
export type Pool = {
  strategy: string;
  max_concurrency: number;
  cooldown: number;
  acquire_timeout: number;
  strategy_persisted?: boolean;
};

export type Setter<T> = Dispatch<SetStateAction<T>>;
export type Act = (
  action: () => Promise<void>,
  message?: string,
) => Promise<void>;
export type Ask = (text: string, action: () => Promise<unknown>) => void;
export type Detail = { title: string; body: unknown };
export type ModalKind = "import" | "login" | "key" | null;
