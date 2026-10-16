import { t, useLocale, formatNumber } from "../i18n";
import { useState } from "react";
import { getChart, type ChartView } from "../api/endpoints";
import { useInfiniteQuery } from "@tanstack/react-query";
import { ErrorMessage } from "./ErrorMessage";

/**
 * Controlled chart rendering (spec section 25).
 *
 * Only the kinds the spec allows are drawn, the spec's fields are the only
 * source of data, and colours never carry meaning on their own: every bar is
 * paired with a signed label and the same numbers are always available in the
 * table view. No formatter, no HTML and no URL from the payload is executed.
 */

/**
 * Display formatting only. A bar is a length, not a container for its own
 * label - the signed value is text next to it, so a narrow panel cannot make
 * the two overlap. The exact Decimal strings stay in `title` and in the table
 * view, so rounding never hides the audited number.
 */
function formatAmount(raw: string): string {
  const value = Number(raw);
  if (!Number.isFinite(value)) return raw;
  const rounded = formatNumber(value, { maximumFractionDigits: 2 });
  return `${value < 0 ? "▼" : "▲"} ${rounded}`;
}

function formatShare(raw: string | null): string | null {
  if (!raw) return null;
  const value = Number(raw);
  if (!Number.isFinite(value)) return raw;
  return formatNumber(value, { style: "percent", maximumFractionDigits: 1 });
}

export function ChartPanel({
  chart,
  onDrilldown,
  busy,
}: {
  chart: ChartView;
  onDrilldown?: (dimension: string, value: string) => void;
  busy?: boolean;
}) {
  useLocale();
  const [showTable, setShowTable] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const pages = useInfiniteQuery({
    queryKey: ["chart-pages", chart.id], initialPageParam: chart.offset,
    queryFn: ({ pageParam }) => getChart(chart.id, 200, pageParam),
    initialData: { pages: [chart], pageParams: [chart.offset] }, enabled: false,
    getNextPageParam: last => last.data.length && last.offset + last.data.length < last.total_points
      ? last.offset + last.data.length : undefined,
  });
  const spec = chart.spec;
  const data = pages.data.pages.flatMap(page => page.data);
  const unit = spec.encoding.y.unit ?? "";

  if (!data.length) {
    return (
      <section
        className="card"
        aria-label={spec.title}
        data-testid="chart-panel"
      >
        <div className="card-head">
          <h2>{spec.title}</h2>
        </div>
        <p className="notice">{chart.empty_state || spec.empty_state}</p>
      </section>
    );
  }

  const ranked = [...data].sort((a, b) => Math.abs(Number(b.delta)) - Math.abs(Number(a.delta)));
  const visible = expanded ? ranked : ranked.slice(0, 10);
  const values = data.map((row) => Number(row.delta));
  const extent = Math.max(...values.map((value) => Math.abs(value)), 1);
  const drill = spec.interaction.drilldown_dimensions;
  // The bounded read window is not reflected in `truncated` (that flag covers
  // the stored chart's own cap), so the panel reports the missing points itself
  // rather than letting a "共 N 点" footer contradict the rows on screen.
  const notShown = Math.max(0, chart.total_points - visible.length - chart.offset);
  const droppedByCap = chart.truncated ? chart.dropped_points : 0;

  return (
    <section className="card" aria-label={spec.title} data-testid="chart-panel">
      <div className="card-head">
        <h2>{spec.title}</h2>
        <button
          className="ghost"
          data-testid="chart-toggle-table"
          onClick={() => setShowTable((value) => !value)}
        >
          {showTable ? t("显示图表") : t("显示表格")}
        </button>
      </div>
      {data.length > 10 ? <button className="ghost" data-testid="chart-expand"
        aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
        {expanded ? t("收起为前 10 个分组") : t("展开已加载的全部分组")}
      </button> : null}
      {showTable ? (
        <table className="chart-table" data-testid="chart-table">
          <caption className="muted small">
            {spec.encoding.x.field} → {spec.encoding.y.field}
            {t("，单位")} {unit || t("按指标契约")}
          </caption>
          <thead>
            <tr>
              <th scope="col">{spec.encoding.x.field}</th>
              <th scope="col">{spec.encoding.y.field}</th>
              <th scope="col">{t("净变化占比")}</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => {
              const share = formatShare(row.net_change_share);
              return (
                <tr key={row.key}>
                  <th scope="row">{row.key}</th>
                  <td title={`${row.delta}${unit ? ` ${unit}` : ""}`}>
                    {formatAmount(row.delta)}
                    {unit ? ` ${unit}` : ""}
                  </td>
                  <td title={row.net_change_share ?? undefined}>
                    {share ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <ul className="chart-bars" data-testid="chart-bars">
          {visible.map((row) => {
            const value = Number(row.delta);
            const width = Math.max(
              2,
              Math.round((Math.abs(value) / extent) * 100),
            );
            const share = formatShare(row.net_change_share);
            return (
              <li key={row.key}>
                <div className="chart-row-head">
                  <span className="chart-label">{row.key}</span>
                  <span
                    className="chart-value"
                    title={`${row.delta}${unit ? ` ${unit}` : ""}`}
                  >
                    {formatAmount(row.delta)}
                    {unit ? ` ${unit}` : ""}
                  </span>
                  {share ? (
                    <span
                      className="chart-share muted small"
                      title={row.net_change_share ?? undefined}
                    >
                      {t("净变化占比")} {share}
                    </span>
                  ) : (
                    <span className="muted small">
                      {t("基准为零，无法计算占比")}
                    </span>
                  )}
                  {onDrilldown && drill.length ? (
                    <button
                      className="ghost small"
                      data-testid="chart-drilldown"
                      disabled={busy}
                      onClick={() =>
                        row.dimension_values[0] != null &&
                        onDrilldown(drill[0], String(row.dimension_values[0]))
                      }
                    >
                      {t("下钻")} {drill[0]}
                    </button>
                  ) : null}
                </div>
                <span
                  className={`chart-bar ${value < 0 ? "negative" : "positive"}`}
                  style={{ width: `${width}%` }}
                  aria-hidden="true"
                />
              </li>
            );
          })}
        </ul>
      )}
      {expanded && pages.hasNextPage ? <button className="ghost" data-testid="chart-load-more"
        disabled={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>
        {pages.isFetchingNextPage ? t("加载中…") : t("加载更多分组")}
      </button> : null}
      {pages.error ? <p className="notice error" role="alert"><ErrorMessage error={pages.error} /></p> : null}
      {notShown || droppedByCap ? (
        <p className="notice warn">
          {[
            notShown
              ? t("按变化量从大到小只显示前 {0} 个分组，另有 {1} 个未显示", {
                  0: visible.length,
                  1: notShown,
                })
              : null,
            droppedByCap
              ? t("存储图表时已丢弃 {0} 个分组", { 0: droppedByCap })
              : null,
          ]
            .filter(Boolean)
            .join("；")}
          {t("（未合并、未补零）。")}
        </p>
      ) : null}
      <p className="muted small">
        {unit ? t("单位 {0} · ", { 0: unit }) : ""}
        {t("chart.footer", {
          evidence: spec.evidence_ids.length,
          type: spec.data_ref.type,
          id: spec.data_ref.id.slice(0, 8),
          shown: visible.length,
          total: chart.total_points,
        })}
      </p>
    </section>
  );
}
