/**
 * CalculationsPage - Grid view of historical calculations
 * Shows all completed jobs with histograms and download options
 */

import React, { useState, useEffect } from 'react';
import { useVegetationApi } from '../../services/api';
import { useVegetationContext } from '../../services/vegetationContext';
import { CalculationCard } from '../widgets/CalculationCard';
import { VegetationJob } from '../../types';
import { Loader2, Search, Filter, Grid, List, RefreshCw } from 'lucide-react';

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
    const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
    const [filterIndex, setFilterIndex] = useState<string>('all');
    const [searchQuery, setSearchQuery] = useState('');

    // Fetch jobs
    const fetchJobs = async () => {
        setLoading(true);
        setError(null);
        try {
            const response = await api.listJobs('completed', 50, 0);
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

    // Handle view in map
    const handleViewInMap = (job: VegetationJob) => {
        if (job.entity_id) {
            setSelectedEntityId(job.entity_id);
        }
        if (job.scene_id) {
            setSelectedSceneId(job.scene_id);
        }
        onViewInMap?.(job);
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

    // Handle delete
    const handleDelete = async (job: VegetationJob) => {
        try {
            await api.deleteJob(job.id);
            // Remove from local state
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

                {/* View toggle */}
                <div className="flex items-center bg-slate-100 rounded-lg p-1">
                    <button
                        onClick={() => setViewMode('grid')}
                        className={`p-2 rounded-md transition-colors ${viewMode === 'grid' ? 'bg-white shadow-sm text-green-700' : 'text-slate-500'
                            }`}
                        title="Vista cuadrícula"
                    >
                        <Grid className="w-4 h-4" />
                    </button>
                    <button
                        onClick={() => setViewMode('list')}
                        className={`p-2 rounded-md transition-colors ${viewMode === 'list' ? 'bg-white shadow-sm text-green-700' : 'text-slate-500'
                            }`}
                        title="Vista lista"
                    >
                        <List className="w-4 h-4" />
                    </button>
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

            {/* Grid/List of calculations */}
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
                <div className={
                    viewMode === 'grid'
                        ? 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4'
                        : 'flex flex-col gap-3'
                }>
                    {filteredJobs.map(job => (
                        <CalculationCard
                            key={job.id}
                            job={job}
                            onViewInMap={handleViewInMap}
                            onDownload={handleDownload}
                            onDelete={handleDelete}
                        />
                    ))}
                </div>
            )}
        </div>
    );
};

export default CalculationsPage;
