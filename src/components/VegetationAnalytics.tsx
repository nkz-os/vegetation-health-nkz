import React, { useState, useEffect } from 'react';
import { Card } from '@nekazari/ui-kit';
import { useVegetationContext } from '../services/vegetationContext';
import { useVegetationApi } from '../services/api';
import { TimeseriesChart } from './analytics/TimeseriesChart';
import { DistributionHistogram } from './analytics/DistributionHistogram';
import { useAuth } from '../hooks/useAuth';
import type { VegetationJob } from '../types';

export const VegetationAnalytics: React.FC = () => {
    const { selectedIndex, selectedEntityId, setSelectedEntityId } = useVegetationContext();
    const { isAuthenticated } = useAuth();
    const api = useVegetationApi();
    const [recentJobs, setRecentJobs] = useState<VegetationJob[]>([]);
    const [loadingJobs, setLoadingJobs] = useState(false);

    useEffect(() => {
        if (!selectedEntityId && isAuthenticated) {
            setLoadingJobs(true);
            api.listJobs('completed', 20, 0)
                .then(response => {
                    setRecentJobs(response.jobs);
                })
                .catch(console.error)
                .finally(() => setLoadingJobs(false));
        }
    }, [selectedEntityId, isAuthenticated]);

    // Group jobs by entity to show unique parcels
    const uniqueEntities = React.useMemo(() => {
        const map = new Map<string, VegetationJob>();
        recentJobs.forEach(job => {
            if (job.entity_id && !map.has(job.entity_id)) {
                map.set(job.entity_id, job);
            }
        });
        return Array.from(map.values());
    }, [recentJobs]);

    if (!isAuthenticated) {
        return (
            <div className="flex items-center justify-center h-64">
                <p className="text-gray-500">Please log in to view analytics.</p>
            </div>
        );
    }

    if (!selectedEntityId) {
        return (
            <div className="space-y-6 max-w-4xl mx-auto py-8">
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

            {/* Resolution Warning - Visible for small entities */}
            {(selectedIndex && ['NDVI', 'EVI'].includes(selectedIndex)) && (
                <div className="bg-amber-50 border-l-4 border-amber-400 p-4 rounded-md mb-6">
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
                <div className="mb-4">
                    <h3 className="text-lg font-semibold text-slate-800">Tendencias de Vegetación ({selectedIndex || 'NDVI'})</h3>
                    <p className="text-sm text-slate-500">Evolución histórica del índice</p>
                </div>
                <div className="h-64 bg-slate-50 rounded-lg flex items-center justify-center">
                    {/* Placeholder for real component */}
                    <TimeseriesChart series={[]} indexType={selectedIndex || 'NDVI'} />
                </div>
            </Card>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Year-over-Year Comparison */}
                <Card padding="lg" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl shadow-sm">
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
                                {/* Mock Data for Demo - Replace with real API data */}
                                {[2024, 2023, 2022].map((year, i) => (
                                    <tr key={year}>
                                        <td className="px-3 py-2 whitespace-nowrap text-sm font-medium text-gray-900">{year}</td>
                                        <td className="px-3 py-2 whitespace-nowrap text-sm text-gray-500">0.{62 - i * 4}</td>
                                        <td className={`px-3 py-2 whitespace-nowrap text-sm font-bold ${i === 0 ? 'text-green-600' : 'text-red-600'}`}>
                                            {i === 0 ? '+4%' : '-2%'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </Card>

                {/* Stats Summary */}
                <Card padding="lg" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl shadow-sm">
                    <div className="mb-4">
                        <h3 className="text-md font-semibold text-slate-800">Estadísticas Rápidas</h3>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div className="p-3 bg-green-50 rounded-lg border border-green-100">
                            <span className="block text-xs text-green-600 uppercase font-bold">Max {selectedIndex}</span>
                            <span className="text-2xl font-bold text-green-700">0.86</span>
                        </div>
                        <div className="p-3 bg-blue-50 rounded-lg border border-blue-100">
                            <span className="block text-xs text-blue-600 uppercase font-bold">Media</span>
                            <span className="text-2xl font-bold text-blue-700">0.62</span>
                        </div>
                        <div className="p-3 bg-amber-50 rounded-lg border border-amber-100">
                            <span className="block text-xs text-amber-600 uppercase font-bold">Min</span>
                            <span className="text-2xl font-bold text-amber-700">0.12</span>
                        </div>
                        <div className="p-3 bg-purple-50 rounded-lg border border-purple-100">
                            <span className="block text-xs text-purple-600 uppercase font-bold">Std Dev</span>
                            <span className="text-2xl font-bold text-purple-700">0.15</span>
                        </div>
                    </div>
                </Card>
            </div>
        </div>
    );
};
