/**
 * SmartTimeline - time-scaled strip of the acquisitions of one index.
 *
 * Each acquisition is a dot placed by its date on a proportional axis, so
 * gaps between acquisitions read as gaps in time. Dense series scroll
 * horizontally instead of overlapping, and the selected dot is kept in view.
 * Arrow keys move to the previous/next acquisition.
 *
 * Positioning uses inline styles: the host's Tailwind build never scans module
 * sources, so only utilities the host already ships can be used as classes.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CloudOff } from 'lucide-react';
import { useTranslation } from '@nekazari/sdk';

export interface TickData {
  /** Scene UUID; null for acquisitions without a scene (Copernicus). */
  scene_id: string | null;
  sensing_date: string;
  mean_value: number | null;
  cloud_coverage?: number | null;
}

interface SmartTimelineProps {
  stats: TickData[];
  /** Currently selected acquisition date (YYYY-MM-DD) */
  selectedDate?: string | null;
  onDateSelect?: (date: string, sceneId: string | null) => void;
  indexType?: string;
  isLoading?: boolean;
}

const DAY_MS = 86_400_000;
/** Minimum horizontal room per acquisition before the strip starts scrolling. */
const MIN_PX_PER_TICK = 18;
const EDGE_PAD_PX = 12;

const toUtcMs = (iso: string): number => {
  const [y, m, d] = iso.split('-').map(Number);
  return Date.UTC(y, (m || 1) - 1, d || 1);
};

function getTickColor(meanValue: number | null, indexType: string): string {
  if (indexType.startsWith('SAR')) return '#818cf8'; // backscatter (dB): no vigour scale
  if (meanValue == null) return '#94a3b8';
  if (meanValue >= 0.6) return '#22c55e';
  if (meanValue >= 0.3) return '#eab308';
  return '#ef4444';
}

const formatDay = (iso: string): string =>
  new Date(toUtcMs(iso)).toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' });

const formatMonth = (ms: number): string =>
  new Date(ms).toLocaleDateString(undefined, { month: 'short', year: '2-digit', timeZone: 'UTC' });

export const SmartTimeline: React.FC<SmartTimelineProps> = ({
  stats,
  selectedDate,
  onDateSelect,
  indexType = 'NDVI',
  isLoading = false,
}) => {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState<TickData | null>(null);

  const ticks = useMemo(
    () => [...stats].sort((a, b) => a.sensing_date.localeCompare(b.sensing_date)),
    [stats],
  );

  const { min, span, months } = useMemo(() => {
    if (ticks.length === 0) return { min: 0, span: 1, months: [] as number[] };
    const lo = toUtcMs(ticks[0].sensing_date);
    const hi = toUtcMs(ticks[ticks.length - 1].sensing_date);
    // Pad a single acquisition (or a same-day series) so it sits centred.
    const pad = hi === lo ? 15 * DAY_MS : 0;
    const start = lo - pad;
    const end = hi + pad;
    const ms: number[] = [];
    const first = new Date(start);
    let cursor = Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 1);
    while (cursor <= end) {
      ms.push(cursor);
      const c = new Date(cursor);
      cursor = Date.UTC(c.getUTCFullYear(), c.getUTCMonth() + 1, 1);
    }
    return { min: start, span: Math.max(end - start, DAY_MS), months: ms };
  }, [ticks]);

  const pct = useCallback((ms: number) => ((ms - min) / span) * 100, [min, span]);

  const selectedIdx = ticks.findIndex(tk => tk.sensing_date === selectedDate);

  // Keep the selected acquisition visible when the strip scrolls.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || selectedIdx < 0 || el.scrollWidth <= el.clientWidth) return;
    const x = (pct(toUtcMs(ticks[selectedIdx].sensing_date)) / 100) * (el.scrollWidth - 2 * EDGE_PAD_PX);
    el.scrollTo({ left: Math.max(0, x - el.clientWidth / 2), behavior: 'smooth' });
  }, [selectedIdx, ticks, pct]);

  const select = useCallback((tk: TickData) => {
    onDateSelect?.(tk.sensing_date, tk.scene_id ?? null);
  }, [onDateSelect]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (ticks.length === 0) return;
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const from = selectedIdx < 0 ? ticks.length - 1 : selectedIdx;
    const to = e.key === 'ArrowLeft' ? Math.max(0, from - 1) : Math.min(ticks.length - 1, from + 1);
    if (to !== selectedIdx) select(ticks[to]);
  }, [ticks, selectedIdx, select]);

  if (ticks.length === 0) {
    return (
      <div className="flex items-center justify-center gap-nkz-inline py-nkz-inline text-nkz-sm text-nkz-text-muted">
        {isLoading ? (
          <span>{t('timeline.loadingHistory')}</span>
        ) : (
          <>
            <CloudOff className="w-4 h-4" />
            <span>{t('timeline.noDataAvailable')}</span>
          </>
        )}
      </div>
    );
  }

  const info = hovered ?? (selectedIdx >= 0 ? ticks[selectedIdx] : null);

  return (
    <div style={{ opacity: isLoading ? 0.6 : 1, transition: 'opacity 150ms' }}>
      <div
        ref={scrollRef}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        role="listbox"
        aria-label={t('timeline.evolution', { index: indexType })}
        className="focus:outline-none"
        style={{ overflowX: 'auto', overflowY: 'hidden' }}
      >
        <div
          className="relative"
          style={{
            height: 44,
            minWidth: ticks.length * MIN_PX_PER_TICK + 2 * EDGE_PAD_PX,
            margin: `0 ${EDGE_PAD_PX}px`,
          }}
        >
          {/* Axis */}
          <div
            className="absolute bg-nkz-border"
            style={{ left: 0, right: 0, top: 14, height: 2, borderRadius: 1 }}
          />

          {/* Month gridlines + labels */}
          {months.map(ms => (
            <div key={ms} className="absolute" style={{ left: `${pct(ms)}%`, top: 8, bottom: 0 }}>
              <div className="bg-nkz-border" style={{ width: 1, height: 14 }} />
              <span
                className="absolute text-nkz-text-muted whitespace-nowrap"
                style={{ top: 18, left: 3, fontSize: 10 }}
              >
                {formatMonth(ms)}
              </span>
            </div>
          ))}

          {/* Acquisitions */}
          {ticks.map((tk, i) => {
            const isSelected = i === selectedIdx;
            const color = getTickColor(tk.mean_value, indexType);
            const size = isSelected ? 14 : 10;
            return (
              <button
                key={tk.scene_id ?? tk.sensing_date}
                type="button"
                role="option"
                aria-selected={isSelected}
                onClick={() => select(tk)}
                onMouseEnter={() => setHovered(tk)}
                onMouseLeave={() => setHovered(null)}
                title={`${formatDay(tk.sensing_date)} · ${indexType} ${tk.mean_value?.toFixed(3) ?? '–'}`}
                className="absolute rounded-full cursor-pointer focus:outline-none"
                style={{
                  left: `${pct(toUtcMs(tk.sensing_date))}%`,
                  top: 15 - size / 2,
                  width: size,
                  height: size,
                  marginLeft: -size / 2,
                  backgroundColor: color,
                  boxShadow: isSelected ? `0 0 0 2px rgb(var(--nkz-color-surface-rgb, 255 255 255)), 0 0 0 4px ${color}` : 'none',
                  transition: 'all 120ms ease-out',
                  zIndex: isSelected ? 2 : 1,
                }}
              />
            );
          })}
        </div>
      </div>

      {/* Readout of the hovered / selected acquisition — fixed height, no layout jump */}
      <div
        className="flex items-center justify-center gap-nkz-inline text-nkz-xs text-nkz-text-muted"
        style={{ minHeight: 18 }}
      >
        {info && (
          <>
            <span className="text-nkz-text-primary">{formatDay(info.sensing_date)}</span>
            <span>
              {indexType} {info.mean_value != null ? info.mean_value.toFixed(3) : '–'}
            </span>
            {info.cloud_coverage != null && (
              <span>
                {t('timeline.clouds')} {Number(info.cloud_coverage).toFixed(0)}%
              </span>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default SmartTimeline;
