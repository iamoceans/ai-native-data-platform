import { setLocale, useLocale } from "../i18n";

export function LanguageSwitcher() {
  const locale = useLocale();
  return (
    <label className="language-switcher">
      <span>{locale === "zh-CN" ? "语言" : "Language"}</span>
      <select
        aria-label={locale === "zh-CN" ? "界面语言" : "Interface language"}
        data-testid="language-switcher"
        value={locale}
        onChange={(event) => setLocale(event.target.value as "zh-CN" | "en")}
      >
        <option value="zh-CN" lang="zh-CN">
          简体中文
        </option>
        <option value="en" lang="en">
          English
        </option>
      </select>
    </label>
  );
}
