/**
 * Smart Timeline Component
 * Combines a date slider with a line chart showing index trends over time.
 * Allows visual navigation through historical vegetation data.
 */

import React, { useMemo } from 'react';
import {
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Area,
  ComposedChart,
} from 'recharts';
import { Calendar, TrendingUp, TrendingDown, CloudOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface SceneStats {
  scene_id: string;
  sensing_date: string;
  mean_value: number | null;
  min_value: number | null;
  max_value: number | null;
  std_dev: number | null;
  cloud_coverage: number | null;
}

interface SmartTimelineProps {
  stats: SceneStats[];
  selectedDate: string | null;
  onDateSelect: (date: string, sceneId: string) => void;
  indexType: string;
  previousYearStats?: SceneStats[];
  showComparison?: boolean;
  isLoading?: boolean;
}

// Color maps for different indices
const INDEX_COLORS: Record<string, { primary: string; secondary: string; gradient: string[] }> = {
  NDVI: { primary: '#22c55e', secondary: '#86efac', gradient: ['#dcfce7', '#22c55e'] },
  NDMI: { primary: '#3b82f6', secondary: '#93c5fd', gradient: ['#dbeafe', '#3b82f6'] },
  SAVI: { primary: '#84cc16', secondary: '#bef264', gradient: ['#ecfccb', '#84cc16'] },
  NDRE: { primary: '#f97316', secondary: '#fdba74', gradient: ['#ffedd5', '#f97316'] },
  GNDVI: { primary: '#10b981', secondary: '#6ee7b7', gradient: ['#d1fae5', '#10b981'] },
};

const formatDate = (dateStr: string): string => {
  const date = new Date(dateStr);
  return date.toLocaleDateString('es-ES', { day: 'numeric', month: 'short' });
};

const formatMonth = (dateStr: string): string => {
  const date = new Date(dateStr);
  return date.toLocaleDateString('es-ES', { month: 'short', year: '2-digit' });
};

export const SmartTimeline: React.FC<SmartTimelineProps> = ({
  stats,
  selectedDate,
  onDateSelect,
  indexType,
  previousYearStats,
  showComparison = false,
  isLoading = false,
}) => {
  const { t } = useTranslation();
  const colors = INDEX_COLORS[indexType] || INDEX_COLORS.NDVI;

  // Prepare chart data - reverse to show oldest first (left to right)
  const chartData = useMemo(() => {
    // Filter out entries without mean_value for cleaner chart
    const validStats = stats.filter(s => s.mean_value !== null);

    return validStats.reverse().map((stat) => {
      const prevYearStat = previousYearStats?.find(p => {
        // Match by month/day for comparison
        const currDate = new Date(stat.sensing_date);
        const prevDate = new Date(p.sensing_date);
        return currDate.getMonth() === prevDate.getMonth() &&
          Math.abs(currDate.getDate() - prevDate.getDate()) <= 7;
      });

      return {
        date: stat.sensing_date,
        displayDate: formatDate(stat.sensing_date),
        month: formatMonth(stat.sensing_date),
        value: stat.mean_value,
        min: stat.min_value,
        max: stat.max_value,
        stdDev: stat.std_dev,
        cloud: stat.cloud_coverage,
        sceneId: stat.scene_id,
        prevYearValue: prevYearStat?.mean_value ?? null,
        isSelected: stat.sensing_date === selectedDate,
      };
    });
  }, [stats, previousYearStats, selectedDate]);

  // Calculate trend
  const trend = useMemo(() => {
    if (chartData.length < 2) return null;
    const recent = chartData.slice(-3).filter(d => d.value !== null);
    if (recent.length < 2) return null;
    const diff = (recent[recent.length - 1].value || 0) - (recent[0].value || 0);
    return diff > 0.02 ? 'up' : diff < -0.02 ? 'down' : 'stable';
  }, [chartData]);

  // Stats summary
  const summary = useMemo(() => {
    const values = chartData.filter(d => d.value !== null).map(d => d.value as number);
    if (values.length === 0) return null;
    return {
      current: values[values.length - 1],
      avg: values.reduce((a, b) => a + b, 0) / values.length,
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }, [chartData]);

  const handleChartClick = (data: any) => {
    if (data && data.activePayload && data.activePayload[0]) {
      const point = data.activePayload[0].payload;
      onDateSelect(point.date, point.sceneId);
    }
  };

  if (isLoading) {
    return (
      <div className="bg-white/95 backdrop-blur-sm rounded-xl border border-slate-200/50 p-4">
        <div className="flex items-center justify-center h-40">
          <div className="animate-pulse flex items-center gap-2 text-slate-500">
            <Calendar className="w-5 h-5" />
            <span>{t('timeline.loadingHistory')}</span>
          </div>
        </div>
      </div>
    );
  }

  if (chartData.length === 0) {
    return (
      <div className="bg-white/95 backdrop-blur-sm rounded-xl border border-slate-200/50 p-4">
        <div className="flex items-center justify-center h-40 text-slate-500">
          <CloudOff className="w-5 h-5 mr-2" />
          <span>{t('timeline.noDataAvailable')}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white/95 backdrop-blur-sm rounded-xl border border-slate-200/50 shadow-lg overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div
            className="w-3 h-3 rounded-full"
            style={{ backgroundColor: colors.primary }}
          />
          <h3 className="font-semibold text-slate-800">
            {t('timeline.evolution', { index: indexType })}
          </h3>
          {trend && (
            <span className={`flex items-center gap-1 text-sm px-2 py-0.5 rounded-full ${trend === 'up' ? 'bg-green-100 text-green-700' :
                trend === 'down' ? 'bg-red-100 text-red-700' :
                  'bg-slate-100 text-slate-600'
              }`}>
              {trend === 'up' ? <TrendingUp className="w-3 h-3" /> :
                trend === 'down' ? <TrendingDown className="w-3 h-3" /> : null}
              {trend === 'up' ? t('timeline.improving') : trend === 'down' ? t('timeline.declining') : t('timeline.stable')}
            </span>
          )}
        </div>

        {summary && (
          <div className="flex items-center gap-4 text-xs text-slate-500">
            <span>{t('timeline.current')}: <strong className="text-slate-700">{summary.current?.toFixed(3)}</strong></span>
            <span>{t('timeline.mean')}: <strong>{summary.avg?.toFixed(3)}</strong></span>
            <span>{t('timeline.range')}: {summary.min?.toFixed(2)} - {summary.max?.toFixed(2)}</span>
          </div>
        )}
      </div>

      {/* Chart */}
      <div className="px-2 py-2" style={{ height: 160 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={chartData}
            onClick={handleChartClick}
            margin={{ top: 10, right: 20, left: 0, bottom: 5 }}
          >
            <defs>
              <linearGradient id={`gradient-${indexType}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={colors.primary} stopOpacity={0.3} />
                <stop offset="95%" stopColor={colors.primary} stopOpacity={0} />
              </linearGradient>
            </defs>

            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />

            <XAxis
              dataKey="displayDate"
              tick={{ fontSize: 10, fill: '#64748b' }}
              tickLine={false}
              axisLine={{ stroke: '#e2e8f0' }}
              interval="preserveStartEnd"
            />

            <YAxis
              tick={{ fontSize: 10, fill: '#64748b' }}
              tickLine={false}
              axisLine={false}
              domain={[-0.2, 1]}
              tickFormatter={(v) => v.toFixed(1)}
              width={35}
            />

            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload || !payload[0]) return null;
                const data = payload[0].payload;
                return (
                  <div className="bg-white shadow-lg rounded-lg border border-slate-200 p-3 text-sm">
                    <p className="font-semibold text-slate-800 mb-1">{data.date}</p>
                    <div className="space-y-1 text-slate-600">
                      <p><span className="font-medium">{indexType}:</span> {data.value?.toFixed(4)}</p>
                      {data.cloud !== null && (
                        <p><span className="font-medium">{t('timeline.clouds')}:</span> {data.cloud?.toFixed(1)}%</p>
                      )}
                      {data.prevYearValue !== null && showComparison && (
                        <p className="text-slate-400">
                          {t('timeline.previousYear')}: {data.prevYearValue?.toFixed(4)}
                        </p>
                      )}
                    </div>
                  </div>
                );
              }}
            />

            {/* Filled area under line */}
            <Area
              type="monotone"
              dataKey="value"
              stroke="none"
              fill={`url(#gradient-${indexType})`}
            />

            {/* Previous year comparison line */}
            {showComparison && previousYearStats && (
              <Line
                type="monotone"
                dataKey="prevYearValue"
                stroke={colors.secondary}
                strokeDasharray="5 5"
                strokeWidth={1.5}
                dot={false}
                name={t('timeline.previousYear')}
              />
            )}

            {/* Main data line */}
            <Line
              type="monotone"
              dataKey="value"
              stroke={colors.primary}
              strokeWidth={2.5}
              dot={(props) => {
                const { cx, cy, payload } = props;
                const isSelected = payload.date === selectedDate;
                if (isSelected) {
                  return (
                    <circle
                      key={`dot-${payload.date}`}
                      cx={cx}
                      cy={cy}
                      r={6}
                      fill="white"
                      stroke={colors.primary}
                      strokeWidth={3}
                    />
                  );
                }
                return (
                  <circle
                    key={`dot-${payload.date}`}
                    cx={cx}
                    cy={cy}
                    r={3}
                    fill={colors.primary}
                    style={{ cursor: 'pointer' }}
                  />
                );
              }}
              activeDot={{
                r: 5,
                fill: colors.primary,
                stroke: 'white',
                strokeWidth: 2,
              }}
            />

            {/* Reference line for selected date */}
            {selectedDate && (
              <ReferenceLine
                x={formatDate(selectedDate)}
                stroke={colors.primary}
                strokeDasharray="3 3"
                strokeOpacity={0.5}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Date slider / scene buttons */}
      <div className="px-4 py-2 border-t border-slate-100 bg-slate-50/50">
        <div className="flex gap-1 overflow-x-auto pb-1 scrollbar-thin">
          {chartData.map((point) => (
            <button
              key={point.sceneId}
              onClick={() => onDateSelect(point.date, point.sceneId)}
              className={`
                flex-shrink-0 px-2 py-1 rounded-md text-xs transition-all
                ${point.isSelected
                  ? 'bg-slate-800 text-white shadow-sm'
                  : 'bg-white hover:bg-slate-100 text-slate-600 border border-slate-200'
                }
              `}
            >
              {point.displayDate}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};

export default SmartTimeline;
