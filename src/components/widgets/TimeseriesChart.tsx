import React, { useEffect, useState } from 'react';
import { useVegetationApi } from '../../hooks/useVegetationApi';
import { Loader2, AlertCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface TimeseriesChartProps {
  entityId: string;
  indexType: string;
}

const TimeseriesChart: React.FC<TimeseriesChartProps> = ({ entityId, indexType }) => {
  const { t } = useTranslation();
  const api = useVegetationApi();
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!entityId) return;

    const fetchData = async () => {
      setLoading(true);
      setError(null);
      try {
        // Fetch last 12 months
        const res = await api.getSceneStats(entityId, indexType, 12);
        // Expected response: { stats: [{ date: '...', mean: 0.5 }, ...] }
        setData(res?.stats || []);
      } catch (err) {
        setError(t('timeline.errorLoadingData'));
        console.error(err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [entityId, indexType, api]);

  if (loading) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-50 rounded-lg">
        <Loader2 className="w-6 h-6 text-green-600 animate-spin" />
        <span className="ml-2 text-sm text-slate-500">{t('timeline.loadingData')}</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-red-50 rounded-lg">
        <AlertCircle className="w-6 h-6 text-red-500" />
        <span className="ml-2 text-sm text-red-600">{error}</span>
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-50 rounded-lg border border-dashed border-slate-300">
        <div className="text-center">
          <p className="text-slate-500 font-medium">{t('timeline.noDataTitle')}</p>
          <p className="text-xs text-slate-400">{t('timeline.noDataHint')}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full p-4 bg-white rounded-lg">
      <div className="mb-2 flex justify-between items-end">
        <div>
          <h4 className="text-sm font-semibold text-slate-700">{t('timeline.timeseriesTitle', { index: indexType })}</h4>
          <p className="text-xs text-slate-500">{t('timeline.dataPoints', { count: data.length })}</p>
        </div>
        <div className="text-right">
          <p className="text-2xl font-bold text-green-600">
            {data[data.length - 1]?.mean?.toFixed(2) || '-'}
          </p>
          <p className="text-xs text-slate-400">{t('timeline.latestValue')}</p>
        </div>
      </div>

      {/* Simple Visualization (Bars) */}
      <div className="flex items-end space-x-1 h-48 mt-4 border-b border-l border-slate-200 p-1">
        {data.map((point, i) => {
          const height = (point.mean || 0) * 100; // Assume 0-1 range
          return (
            <div
              key={i}
              className="flex-1 bg-green-500 hover:bg-green-600 transition-all rounded-t-sm relative group"
              style={{ height: `${height}%` }}
              title={`${point.date || point.sensing_date}: ${point.mean?.toFixed(2)}`}
            >
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default TimeseriesChart;
