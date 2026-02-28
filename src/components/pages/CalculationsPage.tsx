/**
 * CalculationsPage - Flat table of historical calculations (§12.4)
 * Columns: Scene date, Index, Status (Success/Clouds/Failed)
 */

import React, { useState, useEffect } from 'react';
import { useVegetationApi } from '../../services/api';
import { useVegetationContext } from '../../services/vegetationContext';
import { VegetationJob } from '../../types';
import { Loader2, Search, Filter, RefreshCw, MapPin, Download, Trash2 } from 'lucide-react';

interface CalculationsPageProps {
    onViewInMap?: (job: VegetationJob) => void;
}

export const CalculationsPage: React.FC<CalculationsPageProps> = ({
    onViewInMap,
}) => {
    const api = useVegetationApi();
    const { setSelectedEntityId, setSelectedSceneId } = useVegetationContext();

    const [jobs, setJobs] = useState<VegetationJob[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [filterIndex, setFilterIndex] = useState<string>('all');
    const [searchQuery, setSearchQuery] = useState('');

    // Fetch jobs
    const fetchJobs = async () => {
        setLoading(true);
        setError(null);
        try {
            const response = await api.listJobs(undefined, 50, 0);
            if (response && response.jobs) {
                setJobs(response.jobs);
            }
        } catch (err) {
            console.error('Error fetching jobs:', err);
            setError('Error al cargar los cálculos');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchJobs();
    }, []);

    // Handle view in map: set context and navigate so host shows map with entity/scene
    const handleViewInMap = (job: VegetationJob) => {
        if (job.entity_id) setSelectedEntityId(job.entity_id);
        if (job.scene_id) setSelectedSceneId(String(job.scene_id));
        onViewInMap?.(job);
        const nav = (window as any).__nekazariNavigate;
        if (typeof nav === 'function') {
            nav(`/vegetation?entityId=${encodeURIComponent(job.entity_id || '')}&tab=analytics`);
        }
    };

    // Handle download
    const handleDownload = async (job: VegetationJob, format: 'geotiff' | 'png' | 'csv') => {
        try {
            // For now, construct the download URL based on the job result
            // In production, this would call an API endpoint
            if (job.result_url) {
                const downloadUrl = format === 'geotiff'
                    ? job.result_url
                    : `${job.result_url.replace('.tif', `.${format}`)}`;

                window.open(downloadUrl, '_blank');
            } else {
                // Call API to generate download
                const blob = await api.downloadResult(job.id, format);
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${job.index_type}_${job.entity_id || 'area'}_${job.created_at?.split('T')[0]}.${format}`;
                a.click();
                URL.revokeObjectURL(url);
            }
        } catch (err) {
            console.error('Download error:', err);
            alert('Error al descargar el archivo');
        }
    };

    // Filter jobs
    const filteredJobs = jobs.filter(job => {
        const matchesIndex = filterIndex === 'all' || job.index_type === filterIndex;
        const matchesSearch = !searchQuery ||
            (job.entity_name?.toLowerCase().includes(searchQuery.toLowerCase())) ||
            (job.entity_id?.toLowerCase().includes(searchQuery.toLowerCase()));
        return matchesIndex && matchesSearch;
    });

    // Get unique index types for filter
    const indexTypes = ['all', ...new Set(jobs.map(j => j.index_type).filter(Boolean))];

    const sceneDate = (job: VegetationJob) =>
        (job.result as any)?.sensing_date || job.completed_at || job.created_at || '—';

    // Domain status: derive from explicit backend flags instead of error message text (§12.4 review)
    const statusLabel = (job: VegetationJob): 'Éxito' | 'Nubes' | 'Fallo' => {
        const result = (job.result as any) || {};
        if (result.skipped_due_to_clouds === true) {
            return 'Nubes';
        }
        if (job.status === 'completed') {
            return 'Éxito';
        }
        if (job.status === 'failed') {
            return 'Fallo';
        }
        return 'Fallo';
    };

    // Handle delete
    const handleDelete = async (job: VegetationJob) => {
        if (!confirm('¿Eliminar este cálculo del historial?')) return;
        try {
            await api.deleteJob(job.id);
            setJobs(prev => prev.filter(j => j.id !== job.id));
        } catch (err) {
            console.error('Delete error:', err);
            alert('Error al eliminar el cálculo');
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <div className="text-center">
                    <Loader2 className="w-8 h-8 animate-spin text-green-600 mx-auto mb-2" />
                    <p className="text-slate-500">Cargando cálculos...</p>
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="flex items-center justify-center h-64">
                <div className="text-center">
                    <p className="text-red-500 mb-4">{error}</p>
                    <button
                        onClick={fetchJobs}
                        className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700"
                    >
                        Reintentar
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="p-6 max-w-7xl mx-auto">
            {/* Header */}
            <div className="mb-6">
                <h1 className="text-2xl font-bold text-slate-900 mb-2">Historial de Cálculos</h1>
                <p className="text-slate-600">
                    Visualiza y descarga los análisis de vegetación realizados anteriormente.
                </p>
            </div>

            {/* Toolbar */}
            <div className="bg-white rounded-xl border border-slate-200 p-4 mb-6 flex flex-wrap items-center gap-4">
                {/* Search */}
                <div className="flex-1 min-w-[200px] relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                    <input
                        type="text"
                        placeholder="Buscar por parcela..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full pl-9 pr-4 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-green-500 focus:border-transparent"
                    />
                </div>

                {/* Index filter */}
                <div className="flex items-center gap-2">
                    <Filter className="w-4 h-4 text-slate-400" />
                    <select
                        value={filterIndex}
                        onChange={(e) => setFilterIndex(e.target.value)}
                        className="text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-green-500"
                    >
                        <option value="all">Todos los índices</option>
                        {indexTypes.filter(t => t !== 'all').map(type => (
                            <option key={type} value={type}>{type}</option>
                        ))}
                    </select>
                </div>

                {/* Refresh */}
                <button
                    onClick={fetchJobs}
                    className="p-2 text-slate-500 hover:text-green-600 hover:bg-slate-100 rounded-lg transition-colors"
                    title="Actualizar"
                >
                    <RefreshCw className="w-4 h-4" />
                </button>
            </div>

            {/* Results count */}
            <div className="mb-4 text-sm text-slate-500">
                {filteredJobs.length} {filteredJobs.length === 1 ? 'resultado' : 'resultados'}
                {filterIndex !== 'all' && ` para ${filterIndex}`}
            </div>

            {/* Flat table: Scene date, Index, Status (§12.4) */}
            {filteredJobs.length === 0 ? (
                <div className="bg-white rounded-xl border border-slate-200 p-12 text-center">
                    <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
                        <Search className="w-8 h-8 text-slate-400" />
                    </div>
                    <h3 className="text-lg font-medium text-slate-800 mb-2">No hay cálculos</h3>
                    <p className="text-slate-500">
                        {searchQuery || filterIndex !== 'all'
                            ? 'No se encontraron cálculos con los filtros aplicados.'
                            : 'Aún no has realizado ningún análisis de vegetación.'}
                    </p>
                </div>
            ) : (
                <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
                    <table className="w-full text-sm">
                        <thead className="bg-slate-50 border-b border-slate-200">
                            <tr>
                                <th className="text-left py-3 px-4 font-medium text-slate-700">Fecha escena</th>
                                <th className="text-left py-3 px-4 font-medium text-slate-700">Índice</th>
                                <th className="text-left py-3 px-4 font-medium text-slate-700">Estado</th>
                                <th className="text-right py-3 px-4 font-medium text-slate-700">Acciones</th>
                            </tr>
                        </thead>
                        <tbody>
                            {filteredJobs.map(job => (
                                <tr key={job.id} className="border-b border-slate-100 hover:bg-slate-50/50">
                                    <td className="py-2.5 px-4 text-slate-700">
                                        {sceneDate(job) !== '—'
                                            ? new Date(sceneDate(job)).toLocaleDateString('es-ES', { day: '2-digit', month: 'short', year: 'numeric' })
                                            : '—'}
                                    </td>
                                    <td className="py-2.5 px-4 font-medium text-slate-800">{job.index_type || '—'}</td>
                                    <td className="py-2.5 px-4">
                                        <span className={
                                            statusLabel(job) === 'Éxito' ? 'text-emerald-600 font-medium' :
                                                statusLabel(job) === 'Nubes' ? 'text-amber-600' : 'text-red-600'
                                        }>
                                            {statusLabel(job)}
                                        </span>
                                    </td>
                                    <td className="py-2.5 px-4 text-right">
                                        <button
                                            onClick={() => handleViewInMap(job)}
                                            className="p-1.5 text-slate-500 hover:text-green-600 rounded"
                                            title="Ver en mapa"
                                        >
                                            <MapPin className="w-4 h-4 inline" />
                                        </button>
                                        {(job.status === 'completed' && !(job.result as any)?.skipped_due_to_clouds) && (
                                        <button
                                            onClick={() => handleDownload(job, 'geotiff')}
                                            className="p-1.5 text-slate-500 hover:text-green-600 rounded"
                                            title="Descargar"
                                        >
                                            <Download className="w-4 h-4 inline" />
                                        </button>
                                        )}
                                        <button
                                            onClick={() => handleDelete(job)}
                                            className="p-1.5 text-slate-500 hover:text-red-600 rounded"
                                            title="Eliminar"
                                        >
                                            <Trash2 className="w-4 h-4 inline" />
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};

export default CalculationsPage;
