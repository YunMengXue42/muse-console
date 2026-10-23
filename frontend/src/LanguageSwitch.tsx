import { setLang, t, useLang } from "./i18n";
import "./language-switch.css";

export function LanguageSwitch() {
  const lang = useLang();
  return (
    <div className="language-switch" role="group" aria-label={t("界面语言")}>
      <button
        type="button"
        aria-pressed={lang === "zh-CN"}
        onClick={() => setLang("zh-CN")}
      >
        中文
      </button>
      <button
        type="button"
        aria-pressed={lang === "en"}
        onClick={() => setLang("en")}
      >
        English
      </button>
    </div>
  );
}
