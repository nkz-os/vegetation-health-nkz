import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useViewer } from '@nekazari/sdk';
import { useUIKit } from '../../hooks/useUIKit';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import TimeseriesChart from '../widgets/TimeseriesChart';
import DistributionHistogram from '../widgets/DistributionHistogram';
import { IndexPillSelector } from '../widgets/IndexPillSelector'; 
import { AlertCircle, Settings } from 'lucide-react';

const AnalyticsPage: React.FC = () => {
    // Hooks from SDK/Host
    const { Card } = useUIKit();
    const { currentDate } = useViewer();
    const { 
        selectedEntityId, 
        selectedIndex, 
        setSelectedIndex,
        indexResults,
    } = useVegetationContext();

    const customIndexOptions = useMemo(
        () =>
            Object.keys(indexResults || {})
                .filter((k) => k.startsWith('custom:'))
                .map((k) => ({
                    key: k,
                    label: indexResults[k]?.formula_name || k.replace(/^custom:/, '').slice(0, 8),
                })),
        [indexResults],
    );
    const api = useVegetationApi();

    // Local State
    const [activeTab, setActiveTab] = useState<'overview' | 'advanced'>('overview');
    const [stats, setStats] = useState<any[]>([]); // SceneStats[]

    // Load Stats for Histogram
    const loadStats = useCallback(async () => {
        if (!selectedEntityId) return;

        try {
            const data = await api.getSceneStats(selectedEntityId, selectedIndex || 'NDVI', 12);
            setStats(data?.stats || []);
        } catch (err) {
            console.error('Failed to load stats:', err);
        }
    }, [selectedEntityId, selectedIndex, api]);

    useEffect(() => {
        loadStats();
    }, [loadStats]);

    if (!selectedEntityId) {
        return (
            <div className="flex flex-col items-center justify-center h-96">
                <AlertCircle className="w-12 h-12 text-slate-300 mb-4" />
                <p className="text-slate-500 text-lg">Selecciona una parcela para ver sus datos analíticos</p>
                <p className="text-slate-400 text-sm mt-2">Usa el mapa o la lista de parcelas</p>
            </div>
        );
    }

    return (
        <div className="p-6 space-y-6 pb-24">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                   <h1 className="text-2xl font-bold text-gray-900">Análisis de Vegetación</h1>
                   <p className="text-gray-500 text-sm">Parcela: {selectedEntityId}</p>
                   {/* Dummy usage to silence unused variable warning if strict */}
                   <span className="hidden">{currentDate?.toString()}</span>
                </div>
                <div className="flex items-center space-x-2 bg-slate-100 p-1 rounded-lg">
                    <button
                        onClick={() => setActiveTab('overview')}
                        className={`px-3 py-1.5 rounded-md text-sm font-medium transition-all ${
                            activeTab === 'overview' ? 'bg-white shadow text-green-700' : 'text-slate-600 hover:text-green-600'
                        }`}
                    >
                        Vista General
                    </button>
                    <button
                        onClick={() => setActiveTab('advanced')}
                        className={`px-3 py-1.5 rounded-md text-sm font-medium transition-all ${
                            activeTab === 'advanced' ? 'bg-white shadow text-green-700' : 'text-slate-600 hover:text-green-600'
                        }`}
                    >
                        Avanzado
                    </button>
                    <button className="p-1.5 text-slate-400 hover:text-slate-600 rounded-md hover:bg-slate-200" title="Configurar">
                        <Settings className="w-4 h-4" />
                    </button>
                </div>
            </div>

            {/* Content Content - Rendered with Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                
                {/* Left Column (Charts and Histograms) - Spans 2 cols on large screens */}
                {activeTab === 'overview' && (
                  <div className="lg:col-span-3 space-y-6">
                    {/* Main Chart */}
                    <Card padding="lg">
                      <div className="flex items-center justify-between mb-6">
                        <h2 className="text-xl font-semibold text-gray-900">Evolución Temporal</h2>
                        <div className="flex items-center space-x-2">
                          <IndexPillSelector 
                            selectedIndex={selectedIndex || 'NDVI'} 
                            onIndexChange={(idx) => setSelectedIndex(idx)} 
                            customIndexOptions={customIndexOptions}
                          />
                        </div>
                      </div>
                      
                      <div className="h-[400px]">
                         <TimeseriesChart 
                           entityId={selectedEntityId || ''} 
                           indexType={selectedIndex || 'NDVI'} 
                         />
                      </div>
                    </Card>
            
                    {/* Histogram */}
                    <Card padding="lg">
                      <h2 className="text-xl font-semibold text-gray-900 mb-4">Distribución de Valores</h2>
                      <DistributionHistogram 
                        stats={stats} 
                        indexType={selectedIndex || 'NDVI'} 
                      />
                    </Card>
                  </div>
                )}
                
                {activeTab === 'advanced' && (
                    <Card padding="lg">
                        <h2 className="text-xl font-semibold text-gray-900 mb-4">Configuración Avanzada</h2>
                        <p className="text-gray-600">Configuración avanzada de cálculos (próximamente)</p>
                    </Card>
                )}
            </div>
        </div>
    );
};

export default AnalyticsPage;
