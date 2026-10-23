// Existing tests assert the Chinese UI. jsdom reports en-US, so pin Chinese unless a test opts in.
import { beforeEach } from "vitest";
import { setLang } from "./i18n";

beforeEach(() => {
  setLang("zh-CN");
});
