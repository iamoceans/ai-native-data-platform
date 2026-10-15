import { useState } from "react";
import { errorMessage, t, useLocale } from "../i18n";

/** Keep original diagnostics available to keyboard and touch users as well. */
export function ErrorMessage({
  error,
}: {
  error: { code?: string; message?: string };
}) {
  useLocale();
  const [expanded, setExpanded] = useState(false);
  const text = errorMessage(error);
  return (
    <>
      {text}
      {error.message && text !== error.message ? (
        <span className="error-detail">
          {" "}
          <button
            type="button"
            className="ghost small"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {t("Original error")}
          </button>
          {expanded ? (
            <span className="original-error">{error.message}</span>
          ) : null}
        </span>
      ) : null}
    </>
  );
}
