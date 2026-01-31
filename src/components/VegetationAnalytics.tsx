import React, { useState, useEffect } from 'react';
import { Card } from '@nekazari/ui-kit';
import { useVegetationContext } from '../services/vegetationContext';
import { useVegetationApi } from '../services/api';
import { TimeseriesChart } from './analytics/TimeseriesChart';
import { DistributionHistogram } from './analytics/DistributionHistogram';
import { useAuth } from '../hooks/useAuth';
import type { VegetationJob } from '../types';

import { SetupWizard } from './pages/SetupWizard';
import { Button } from '@nekazari/ui-kit'; // Ensure Button is imported

// ... imports

export const VegetationAnalytics: React.FC = () => {
    const { selectedIndex, selectedEntityId, setSelectedEntityId, selectedGeometry } = useVegetationContext();
    const { isAuthenticated } = useAuth();
    const api = useVegetationApi();
    const [recentJobs, setRecentJobs] = useState<VegetationJob[]>([]);
    const [loadingJobs, setLoadingJobs] = useState(false);

    // Subscription state
    const [subscription, setSubscription] = useState<any>(null);
    const [checkingSub, setCheckingSub] = useState(false);
    const [showWizard, setShowWizard] = useState(false);

    // Real statistics from API
    const [yearComparison, setYearComparison] = useState<any[]>([]);
    const [stats, setStats] = useState<{ mean: number; min: number; max: number; std_dev: number } | null>(null);
    const [loadingStats, setLoadingStats] = useState(false);

    // Check subscription when entity changes
    useEffect(() => {
        if (selectedEntityId && isAuthenticated) {
            setCheckingSub(true);
            api.getSubscriptionForEntity(selectedEntityId)
                .then(setSubscription)
                .catch(() => setSubscription(null))
                .finally(() => setCheckingSub(false));

            // Also load jobs
            setLoadingJobs(true);
            api.listJobs('completed', 20, 0)
                .then(response => {
                    setRecentJobs(response.jobs.filter(j => j.entity_id === selectedEntityId));
                })
                .catch(console.error)
                .finally(() => setLoadingJobs(false));
        } else {
            setSubscription(null);
            // Load global recent jobs if no entity selected
            if (!selectedEntityId && isAuthenticated) {
                setLoadingJobs(true);
                api.listJobs('completed', 20, 0)
                    .then(response => setRecentJobs(response.jobs))
                    .catch(console.error)
                    .finally(() => setLoadingJobs(false));
            }
        }
    }, [selectedEntityId, isAuthenticated]);

    // Load real statistics when entity is selected and subscribed
    useEffect(() => {
        if (selectedEntityId && subscription && subscription.status === 'active') {
            setLoadingStats(true);

            // Load year comparison data
            api.compareYears(selectedEntityId, selectedIndex || 'NDVI')
                .then((data) => {
                    if (data && data.years) {
                        setYearComparison(data.years);
                    }
                })
                .catch((err) => {
                    console.warn('[Analytics] Year comparison not available:', err);
                    setYearComparison([]);
                });

            // Load current stats from scene stats
            api.getSceneStats(selectedEntityId, selectedIndex || 'NDVI', 3)
                .then((data) => {
                    if (data && data.statistics) {
                        setStats(data.statistics);
                    }
                })
                .catch((err) => {
                    console.warn('[Analytics] Stats not available:', err);
                    setStats(null);
                })
                .finally(() => setLoadingStats(false));
        }
    }, [selectedEntityId, subscription, selectedIndex]);

    // Group jobs by entity logic (keep existing)
    const uniqueEntities = React.useMemo(() => {
        if (selectedEntityId) return []; // Don't need this if entity selected
        const map = new Map<string, VegetationJob>();
        if (recentJobs && Array.isArray(recentJobs)) {
            recentJobs.forEach(job => {
                if (job.entity_id && !map.has(job.entity_id)) {
                    map.set(job.entity_id, job);
                }
            });
        }
        return Array.from(map.values());
    }, [recentJobs, selectedEntityId]);

    if (!isAuthenticated) {
        // ... keep existing
        return (
            <div className="flex items-center justify-center h-64">
                <p className="text-gray-500">Please log in to view analytics.</p>
            </div>
        );
    }

    if (!selectedEntityId) {
        // ... keep existing dashboard
        return (
            <div className="space-y-6 max-w-4xl mx-auto py-8">
                {/* ... existing content for no selection ... */}
                <div className="flex items-center justify-center h-32 bg-slate-50 rounded-xl border border-dashed border-slate-300">
                    <div className="text-center">
                        <p className="text-slate-500 font-medium">No parcel selected.</p>
                        <p className="text-xs text-slate-400">Select a recently analyzed parcel below or use the map.</p>
                    </div>
                </div>

                <div>
                    <h3 className="text-lg font-semibold text-slate-800 mb-4">Recently Analyzed Parcels</h3>
                    {loadingJobs ? (
                        <div className="text-center py-4 text-slate-500">Loading history...</div>
                    ) : uniqueEntities.length > 0 ? (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {uniqueEntities.map(job => (
                                <div
                                    key={job.entity_id}
                                    className="cursor-pointer hover:opacity-80 transition-opacity"
                                    onClick={() => setSelectedEntityId(job.entity_id || null)}
                                >
                                    <Card padding="md">
                                        <div className="flex justify-between items-start">
                                            <div>
                                                <p className="font-medium text-slate-900">
                                                    {job.entity_type} {job.entity_id?.substring(0, 8)}...
                                                </p>
                                                <p className="text-xs text-slate-500">
                                                    Last Analysis: {new Date(job.created_at).toLocaleDateString()}
                                                </p>
                                            </div>
                                            <div className="bg-green-100 text-green-800 text-xs px-2 py-1 rounded-full">
                                                {job.job_type}
                                            </div>
                                        </div>
                                    </Card>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <p className="text-slate-500 text-sm">No analysis history found.</p>
                    )}
                </div>
            </div>
        );
    }

    // IF ENTITY SELECTED BUT NOT SUBSCRIBED
    if (!checkingSub && !subscription && selectedEntityId) {
        return (
            <div className="space-y-6 max-w-4xl mx-auto py-8">
                <div className="flex justify-between items-center">
                    <h2 className="text-2xl font-bold text-slate-800">Parcel Analysis</h2>
                    <button
                        onClick={() => setSelectedEntityId(null)}
                        className="text-sm text-slate-500 hover:text-slate-700 underline"
                    >
                        Change Parcel
                    </button>
                </div>

                <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-8 text-center space-y-4">
                    <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto text-slate-400 mb-4">
                        <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                        </svg>
                    </div>
                    <h3 className="text-xl font-semibold text-slate-900">Monitoreo Inactivo</h3>
                    <p className="text-slate-500 max-w-md mx-auto">
                        Esta parcela no tiene el monitoreo automático activado. Configúralo para descargar imágenes históricas y recibir actualizaciones semanales.
                    </p>
                    <div className="pt-4">
                        <Button variant="primary" onClick={() => setShowWizard(true)}>
                            Configurar Monitoreo
                        </Button>
                    </div>
                </div>

                {/* Legacy/Manual Jobs if any */}
                {recentJobs.length > 0 && (
                    <div className="opacity-70">
                        <h4 className="text-md font-semibold text-slate-700 mb-2">Historial Manual</h4>
                        {/* Simple list of manual jobs */}
                        <div className="space-y-2">
                            {recentJobs.map(job => (
                                <div key={job.id} className="bg-slate-50 p-3 rounded flex justify-between text-sm">
                                    <span>{new Date(job.created_at).toLocaleDateString()}</span>
                                    <span className="capitalize">{job.status}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                <SetupWizard
                    open={showWizard}
                    onClose={() => setShowWizard(false)}
                    entityId={selectedEntityId}
                    geometry={selectedGeometry} // Can be null if not from map
                    onComplete={() => {
                        // Refresh subscription status
                        setCheckingSub(true);
                        api.getSubscriptionForEntity(selectedEntityId)
                            .then(setSubscription)
                            .finally(() => setCheckingSub(false));
                    }}
                />
            </div>
        );
    }

    // IF AGREEMENT IS SYNCING (NEW UX)
    if (subscription && (subscription.status === 'syncing' || subscription.status === 'created')) {
        return (
            <div className="space-y-6 max-w-4xl mx-auto py-8">
                <div className="flex justify-between items-center">
                    <h2 className="text-2xl font-bold text-slate-800">Analytics Dashboard</h2>
                    <button
                        onClick={() => setSelectedEntityId(null)}
                        className="text-sm text-slate-500 hover:text-slate-700 underline"
                    >
                        Change Parcel
                    </button>
                </div>

                <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-12 text-center space-y-4">
                    <div className="w-16 h-16 bg-blue-50 rounded-full flex items-center justify-center mx-auto text-blue-500 mb-4">
                        <svg className="w-8 h-8 animate-spin" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                        </svg>
                    </div>
                    <h3 className="text-xl font-semibold text-slate-900">Procesando Histórico</h3>
                    <p className="text-slate-500 max-w-md mx-auto">
                        Estamos descargando y procesando imágenes históricas de Sentinel-2 para tu parcela. Esto puede tomar unos minutos.
                    </p>
                    <div className="pt-4 flex justify-center gap-2">
                        <span className="text-sm text-blue-600 font-medium bg-blue-50 px-3 py-1 rounded-full border border-blue-100">
                            Sincronizando...
                        </span>
                    </div>
                    {/* Optionally show recent jobs progress here if available */}
                </div>
            </div>
        );
    }

    // MONITORED DASHBOARD (Existing view wrapped)
    return (
        <div className="space-y-6 max-w-4xl mx-auto py-8">
            <div className="flex justify-between items-center">
                <div className="flex items-center gap-3">
                    <h2 className="text-2xl font-bold text-slate-800">Analytics Dashboard</h2>
                    <span className="bg-green-100 text-green-800 text-xs px-2 py-1 rounded-full border border-green-200 flex items-center">
                        <span className="w-2 h-2 bg-green-500 rounded-full mr-1 animate-pulse"></span>
                        Monitoreo Activo
                    </span>
                </div>
                <button
                    onClick={() => setSelectedEntityId(null)}
                    className="text-sm text-slate-500 hover:text-slate-700 underline"
                >
                    Change Parcel
                </button>
            </div>

            {/* ... rest of dashboard ... */}
            {/* Resolution Warning ... */}
            {(selectedIndex && ['NDVI', 'EVI'].includes(selectedIndex)) && (
                <div className="bg-amber-50 border-l-4 border-amber-400 p-4 rounded-md mb-6">
                    {/* ... warning content ... */}
                    <div className="flex">
                        <div className="flex-shrink-0">
                            {/* Warning Icon */}
                            <svg className="h-5 w-5 text-amber-400" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                            </svg>
                        </div>
                        <div className="ml-3">
                            <p className="text-sm text-amber-700">
                                <span className="font-bold">Nota de Resolución:</span> Para entidades pequeñas (&lt; 100m²), el índice Sentinel-2 representa "Vigor Zonal" y no salud individual precisa.
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {/* Main Timeseries */}
            <Card padding="lg" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl shadow-sm">
                <div className="mb-4 flex justify-between items-center">
                    <div>
                        <h3 className="text-lg font-semibold text-slate-800">Tendencias de Vegetación ({selectedIndex || 'NDVI'})</h3>
                        <p className="text-sm text-slate-500">Evolución histórica del índice</p>
                    </div>
                    <div className="text-xs text-slate-400">
                        Última actualización: {subscription?.last_run_at ? new Date(subscription.last_run_at).toLocaleDateString() : 'Pendiente'}
                    </div>
                </div>
                <div className="h-64 bg-slate-50 rounded-lg flex items-center justify-center">
                    {/* Placeholder for real component */}
                    <TimeseriesChart series={[]} indexType={selectedIndex || 'NDVI'} />
                </div>
            </Card>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Year-over-Year Comparison */}
                <Card padding="lg" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl shadow-sm">
                    {/* ... existing table ... */}
                    <div className="mb-4">
                        <h3 className="text-md font-semibold text-slate-800">Comparativa Anual</h3>
                        <p className="text-xs text-slate-500">Mismo periodo en años anteriores</p>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="min-w-full divide-y divide-gray-200">
                            <thead>
                                <tr>
                                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Año</th>
                                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Media</th>
                                    <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Dif.</th>
                                </tr>
                            </thead>
                            <tbody className="bg-white divide-y divide-gray-200">
                                {loadingStats ? (
                                    <tr>
                                        <td colSpan={3} className="px-3 py-4 text-center text-sm text-gray-500">
                                            Cargando datos...
                                        </td>
                                    </tr>
                                ) : yearComparison.length > 0 ? (
                                    yearComparison.map((yearData: any, i: number) => (
                                        <tr key={yearData.year}>
                                            <td className="px-3 py-2 whitespace-nowrap text-sm font-medium text-gray-900">{yearData.year}</td>
                                            <td className="px-3 py-2 whitespace-nowrap text-sm text-gray-500">{yearData.mean?.toFixed(2) || '-'}</td>
                                            <td className={`px-3 py-2 whitespace-nowrap text-sm font-bold ${yearData.change_percent > 0 ? 'text-green-600' : yearData.change_percent < 0 ? 'text-red-600' : 'text-gray-500'}`}>
                                                {yearData.change_percent !== undefined ? `${yearData.change_percent > 0 ? '+' : ''}${yearData.change_percent.toFixed(1)}%` : '-'}
                                            </td>
                                        </tr>
                                    ))
                                ) : (
                                    <tr>
                                        <td colSpan={3} className="px-3 py-4 text-center text-sm text-gray-500">
                                            No hay datos históricos suficientes
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </Card>

                {/* Stats Summary */}
                <Card padding="lg" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl shadow-sm">
                    {/* ... existing stats ... */}
                    <div className="mb-4">
                        <h3 className="text-md font-semibold text-slate-800">Estadísticas Rápidas</h3>
                    </div>
                    {loadingStats ? (
                        <div className="flex items-center justify-center h-32 text-gray-500">
                            Cargando estadísticas...
                        </div>
                    ) : stats ? (
                        <div className="grid grid-cols-2 gap-4">
                            <div className="p-3 bg-green-50 rounded-lg border border-green-100">
                                <span className="block text-xs text-green-600 uppercase font-bold">Max {selectedIndex}</span>
                                <span className="text-2xl font-bold text-green-700">{stats.max.toFixed(2)}</span>
                            </div>
                            <div className="p-3 bg-blue-50 rounded-lg border border-blue-100">
                                <span className="block text-xs text-blue-600 uppercase font-bold">Media</span>
                                <span className="text-2xl font-bold text-blue-700">{stats.mean.toFixed(2)}</span>
                            </div>
                            <div className="p-3 bg-amber-50 rounded-lg border border-amber-100">
                                <span className="block text-xs text-amber-600 uppercase font-bold">Min</span>
                                <span className="text-2xl font-bold text-amber-700">{stats.min.toFixed(2)}</span>
                            </div>
                            <div className="p-3 bg-purple-50 rounded-lg border border-purple-100">
                                <span className="block text-xs text-purple-600 uppercase font-bold">Std Dev</span>
                                <span className="text-2xl font-bold text-purple-700">{stats.std_dev.toFixed(2)}</span>
                            </div>
                        </div>
                    ) : (
                        <div className="flex items-center justify-center h-32 text-gray-500">
                            Selecciona una parcela para ver estadísticas
                        </div>
                    )}
                </Card>
            </div>
        </div>
    );
};
