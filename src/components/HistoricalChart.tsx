import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from '@nekazari/sdk';
import { useVegetationApi } from '../services/api';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';

const WINDOW_DAYS = 20;
const DEFAULT_INDEX = 'NDVI';

interface HistoricalChartProps {
  entityId: string;
}

interface ChartDataPoint {
  label: string;
  [key: string]: any;
}

export const HistoricalChart: React.FC<HistoricalChartProps> = ({ entityId }) => {
  const { t } = useTranslation();
  const api = useVegetationApi();
  const [data, setData] = useState<ChartDataPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(DEFAULT_INDEX);
  const [years, setYears] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const history = await api.getParcelHistory(entityId, { index: selectedIndex });
      
      if (!history.data || history.data.length === 0) {
        setData([]);
        setYears([]);
        return;
      }
      
      // Group by year
      const byYear: Record<number, any[]> = {};
      for (const d of history.data) {
        if (!byYear[d.year]) byYear[d.year] = [];
        byYear[d.year].push(d);
      }
      
      const _years = Object.keys(byYear).map(Number).sort().reverse();
      setYears(_years);
      
      // Build chart data: one entry per DOY window
      const chartData: Record<number, any> = {};
      for (const d of history.data) {
        if (!d.observedAt) continue;
        const dt = new Date(d.observedAt);
        const doy = Math.floor((dt.getTime() - new Date(dt.getFullYear(), 0, 0).getTime()) / 86400000);
        const month = dt.toLocaleString('default', { month: 'short' });
        const day = dt.getDate();
        const label = `${month} ${day}`;
        
        if (!chartData[doy]) chartData[doy] = { doy, label };
        chartData[doy][`y${d.year}`] = d[`${selectedIndex.toLowerCase()}Mean`] ?? d.ndviMean;
      }
      
      setData(Object.values(chartData).sort((a: any, b: any) => a.doy - b.doy));
    } catch (err: any) {
      setError(err?.message || 'Failed to load historical data');
      console.error('Failed to load history:', err);
    } finally {
      setLoading(false);
    }
  }, [entityId, selectedIndex, api]);

  useEffect(() => { load(); }, [load]);

  // States
  const [building, setBuilding] = useState(false);
  const [buildMsg, setBuildMsg] = useState<string | null>(null);

  const handleBuild = async () => {
    setBuilding(true);
    setBuildMsg(null);
    try {
      const res = await api.buildHistory(entityId, { index: selectedIndex });
      setBuildMsg(`Job started: ${res.job_id.slice(0, 8)}…`);
    } catch (err: any) {
      setBuildMsg(`Error: ${err?.message || String(err)}`);
    } finally {
      setBuilding(false);
    }
  };

  if (loading) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-4">
        <div className="text-sm text-slate-500 text-center py-8">Loading historical data…</div>
      </div>
    );
  }

  if (!data.length && !error) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold text-slate-700 uppercase tracking-wider">
            Historical {selectedIndex}
          </h3>
          <select
            value={selectedIndex}
            onChange={(e) => setSelectedIndex(e.target.value)}
            className="text-xs px-2 py-1 border border-slate-300 rounded-lg bg-white"
          >
            {['NDVI', 'GNDVI', 'NDRE', 'SAVI', 'EVI'].map(idx => (
              <option key={idx} value={idx}>{idx}</option>
            ))}
          </select>
        </div>
        <div className="text-sm text-slate-500 text-center py-8">
          <p className="mb-3">No historical data yet for this index.</p>
          <button
            onClick={handleBuild}
            disabled={building}
            className="inline-flex items-center gap-1.5 text-sm px-4 py-2 rounded-lg bg-emerald-600 text-white font-semibold hover:bg-emerald-700 disabled:opacity-50"
          >
            {building ? 'Building…' : `Build ${selectedIndex} history`}
          </button>
          {buildMsg && (
            <p className="mt-2 text-xs text-slate-500">{buildMsg}</p>
          )}
        </div>
      </div>
    );
  }

  const colors = ['#22c55e', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6'];

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-bold text-slate-700 uppercase tracking-wider">
          Historical {selectedIndex}
        </h3>
        <div className="flex items-center gap-2">
          <select
            value={selectedIndex}
            onChange={(e) => setSelectedIndex(e.target.value)}
            className="text-xs px-2 py-1 border border-slate-300 rounded-lg bg-white"
          >
            {['NDVI', 'GNDVI', 'NDRE', 'SAVI', 'EVI'].map(idx => (
              <option key={idx} value={idx}>{idx}</option>
            ))}
          </select>
          <button
            onClick={handleBuild}
            disabled={building}
            className="text-xs px-2 py-1 rounded-lg bg-emerald-100 text-emerald-700 hover:bg-emerald-200 disabled:opacity-50"
          >
            {building ? '…' : 'Rebuild'}
          </button>
        </div>
      </div>
      
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={3} />
          <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
          <Tooltip />
          <Legend />
          {years.map((year, i) => (
            <Line
              key={year}
              type="monotone"
              dataKey={`y${year}`}
              name={`${year}`}
              stroke={colors[i % colors.length]}
              strokeWidth={1.5}
              dot={false}
              connectNulls={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      
      <p className="text-[11px] text-slate-400 mt-2">
        {WINDOW_DAYS}-day rolling windows · {years.length} years
        {buildMsg && ` · ${buildMsg}`}
      </p>
    </div>
  );
};
