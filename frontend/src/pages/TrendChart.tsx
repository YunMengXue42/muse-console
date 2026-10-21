import { useState, type CSSProperties } from "react";
import type { Stats } from "../api";
import "./trend-chart.css";

function stamp(t: number, detailed = false, days = false) {
  return new Date(t * 1000).toLocaleString("zh-CN", {
    ...(days || detailed ? { month: "2-digit", day: "2-digit" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function TrendChart({
  series,
  windowValue,
}: {
  series: Stats["series"];
  windowValue: string;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const peak = Math.max(...series.map((p) => p.count), 0);
  const step = Math.max(1, Math.ceil(peak / 4));
  const ceiling = step * Math.min(4, Math.max(1, Math.ceil(peak / step)));
  const ticks = Array.from(
    { length: ceiling / step + 1 },
    (_, i) => ceiling - i * step,
  );
  const activeIndex =
    selected === null ? null : Math.min(selected, series.length - 1);
  const active = activeIndex === null ? null : series[activeIndex];
  const tickIndices = [
    ...new Set([
      0,
      ...[0.25, 0.5, 0.75, 1].map((v) => Math.round((series.length - 1) * v)),
    ]),
  ];
  const interval = series.length > 1 ? (series[1].t - series[0].t) / 60 : null;
  const intervalLabel =
    interval === null
      ? ""
      : interval >= 60
        ? `${interval / 60} 小时 / 格`
        : `${interval} 分钟 / 格`;

  return (
    <div className="trend-chart">
      <div className="trend-meta">
        <div className="trend-readout" aria-live="polite" aria-atomic="true">
          {active ? (
            <>
              <time dateTime={new Date(active.t * 1000).toISOString()}>
                {stamp(active.t, true)}
              </time>
              <span>
                请求 <strong>{active.count}</strong>
              </span>
              <span>
                错误 <strong>{active.errors}</strong>
              </span>
            </>
          ) : (
            <>
              <span>
                单格峰值 <strong>{peak}</strong>
              </span>
              <span className="muted">{intervalLabel}</span>
            </>
          )}
        </div>
        <div className="trend-legend" aria-label="图例">
          <span>
            <i className="trend-request-key" />
            请求
          </span>
          <span>
            <i className="trend-error-key" />
            错误
          </span>
        </div>
      </div>
      <div className="trend-plot">
        <div className="trend-scale" aria-hidden="true">
          {ticks.map((n) => (
            <span key={n}>{n}</span>
          ))}
        </div>
        <div
          className="trend-canvas"
          role="group"
          aria-label="请求与错误数量趋势，左右方向键查看各时段"
          tabIndex={0}
          onBlur={() => setSelected(null)}
          onPointerLeave={() => setSelected(null)}
          onKeyDown={(event) => {
            if (
              !["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(
                event.key,
              )
            )
              return;
            event.preventDefault();
            if (event.key === "Escape") return setSelected(null);
            if (event.key === "Home") return setSelected(0);
            if (event.key === "End") return setSelected(series.length - 1);
            const current =
              activeIndex ?? (event.key === "ArrowLeft" ? series.length : -1);
            setSelected(
              Math.max(
                0,
                Math.min(
                  series.length - 1,
                  current + (event.key === "ArrowLeft" ? -1 : 1),
                ),
              ),
            );
          }}
        >
          <div className="trend-grid" aria-hidden="true">
            {ticks.map((n) => (
              <span key={n} />
            ))}
          </div>
          <div
            className="trend-columns"
            style={{ "--trend-count": series.length } as CSSProperties}
          >
            {series.map((point, index) => (
              <div
                key={point.t}
                className={`trend-slot${activeIndex === index ? " is-selected" : ""}`}
                onPointerEnter={() => setSelected(index)}
                onClick={() => setSelected(index)}
                aria-hidden="true"
              >
                <span className="trend-zero" />
                <span
                  className="trend-request"
                  style={{ height: `${(point.count / ceiling) * 100}%` }}
                />
                <span
                  className="trend-error"
                  style={{ height: `${(point.errors / ceiling) * 100}%` }}
                />
              </div>
            ))}
          </div>
        </div>
        <div className="trend-time-axis" aria-hidden="true">
          {tickIndices.map((index, i) => (
            <span key={index} className={i % 2 ? "trend-tick-secondary" : ""}>
              {stamp(series[index].t, false, windowValue === "7d" || i === 0)}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
