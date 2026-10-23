// English strings keyed by the exact Chinese source text. One file per UI area so
// translations can be edited independently; later spreads win on duplicate keys.
import { app } from "./app";
import { pool } from "./pool";
import { pages } from "./pages";
import { devices } from "./devices";

export const en: Record<string, string> = {
  ...app,
  ...pool,
  ...pages,
  ...devices,
};
