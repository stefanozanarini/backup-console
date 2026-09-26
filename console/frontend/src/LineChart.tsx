// Grafico a linee in SVG: una serie per volume, asse Y unico 0–100%, soglie tratteggiate,
// crosshair + tooltip al passaggio del mouse.
import { useEffect, useMemo, useRef, useState } from "react";

export interface Series {
  label: string;
  points: { t: number; v: number }[];
}

interface Props {
  series: Series[];
  thresholds?: { value: number; label: string }[];
  height?: number;
  unit?: string;
}

const M = { top: 12, right: 64, bottom: 24, left: 36 };
const dayFmt = new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "2-digit" });
const tipFmt = new Intl.DateTimeFormat("it-IT", { dateStyle: "short", timeStyle: "short" });

export function seriesColor(i: number) {
  // Ordine fisso degli slot: il colore segue la serie, non il suo rango.
  return `var(--series-${(i % 8) + 1})`;
}

export default function LineChart({ series, thresholds = [], height = 220, unit = "%" }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { t0, t1, times } = useMemo(() => {
    const all = series.flatMap((s) => s.points.map((p) => p.t));
    const uniq = [...new Set(all)].sort((a, b) => a - b);
    return { t0: uniq[0] ?? 0, t1: uniq[uniq.length - 1] ?? 1, times: uniq };
  }, [series]);

  const iw = width - M.left - M.right;
  const ih = height - M.top - M.bottom;
  const span = Math.max(1, t1 - t0);
  const x = (t: number) => M.left + ((t - t0) / span) * iw;
  const y = (v: number) => M.top + ih - (Math.min(100, Math.max(0, v)) / 100) * ih;

  const dayTicks = useMemo(() => {
    const out: number[] = [];
    const d = new Date(t0);
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() + 1);
    const step = Math.ceil((t1 - t0) / 86400000 / Math.max(1, Math.floor(iw / 70))) || 1;
    for (let t = d.getTime(); t <= t1; t += step * 86400000) out.push(t);
    return out;
  }, [t0, t1, iw]);

  if (times.length === 0) {
    return <div className="empty">Nessun dato nel periodo selezionato</div>;
  }

  function onMove(e: React.MouseEvent<SVGRectElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const t = t0 + ((e.clientX - rect.left) / rect.width) * span;
    let best = times[0];
    for (const tt of times) if (Math.abs(tt - t) < Math.abs(best - t)) best = tt;
    setHover(best);
  }

  const hoverValues =
    hover === null
      ? []
      : series.map((s, i) => ({ i, label: s.label, p: s.points.find((p) => p.t === hover) }));
  const tipLeft = hover === null ? 0 : x(hover) > width / 2 ? x(hover) - 180 : x(hover) + 12;

  return (
    <div>
      {series.length >= 2 && (
        <div className="legend">
          {series.map((s, i) => (
            <span key={s.label}>
              <span className="sw" style={{ background: seriesColor(i) }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
      <div className="chart" ref={wrap}>
        <svg height={height} role="img" aria-label="Andamento spazio occupato">
          <g className="axis">
            {[0, 25, 50, 75, 100].map((v) => (
              <g key={v}>
                <line className="gridline" x1={M.left} x2={M.left + iw} y1={y(v)} y2={y(v)} />
                <text x={M.left - 6} y={y(v) + 4} textAnchor="end">{v}{unit}</text>
              </g>
            ))}
            {dayTicks.map((t) => (
              <text key={t} x={x(t)} y={height - 6} textAnchor="middle">{dayFmt.format(t)}</text>
            ))}
          </g>
          {thresholds.map((th) => (
            <g key={th.label}>
              <line className="threshold" x1={M.left} x2={M.left + iw} y1={y(th.value)} y2={y(th.value)} />
              <text className="threshold-label" x={M.left + iw + 6} y={y(th.value) + 4}>{th.label} {th.value}{unit}</text>
            </g>
          ))}
          {series.map((s, i) => (
            <polyline
              key={s.label}
              className="series-line"
              stroke={seriesColor(i)}
              points={s.points.map((p) => `${x(p.t)},${y(p.v)}`).join(" ")}
            />
          ))}
          {series.map((s, i) =>
            s.points.length === 1 ? (
              <circle key={`${s.label}-single`} cx={x(s.points[0].t)} cy={y(s.points[0].v)} r={4} fill={seriesColor(i)} />
            ) : null,
          )}
          {hover !== null && (
            <g>
              <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={M.top} y2={M.top + ih} />
              {hoverValues.map(({ i, p }) =>
                p ? <circle key={i} className="hover-dot" cx={x(p.t)} cy={y(p.v)} r={4} fill={seriesColor(i)} /> : null,
              )}
            </g>
          )}
          <rect
            x={M.left} y={M.top} width={iw} height={ih} fill="transparent"
            onMouseMove={onMove} onMouseLeave={() => setHover(null)}
          />
        </svg>
        {hover !== null && (
          <div className="chart-tooltip" style={{ left: tipLeft, top: M.top }}>
            <div className="t">{tipFmt.format(hover)}</div>
            {hoverValues.map(({ i, label, p }) => (
              <div className="row" key={label}>
                <span><span className="sw" style={{ display: "inline-block", width: 10, height: 3, background: seriesColor(i), marginRight: 6, verticalAlign: "middle" }} />{label}</span>
                <span className="v">{p ? `${p.v.toFixed(1)}${unit}` : "—"}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
