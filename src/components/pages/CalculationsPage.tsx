/**
 * CalculationsPage - Job management panel with list, status, and delete.
 * Shows ALL jobs (pending, running, completed, failed) with bulk cleanup.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useVegetationApi } from '../../services/api';
import { VegetationJob } from '../../types';
import { Loader2, RefreshCw, Trash2, AlertCircle, CheckCircle, Clock, Play, XCircle } from 'lucide-react';

export const CalculationsPage: React.FC = () => {
    const api = useVegetationApi();
    const [jobs, setJobs] = useState<VegetationJob[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [deleting, setDeleting] = useState<string | null>(null);
    const [bulkDeleting, setBulkDeleting] = useState(false);

    const fetchJobs = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const response = await api.listJobs(undefined, 100, 0);
            if (response && response.jobs) {
                setJobs(response.jobs);
            }
        } catch (err) {
            console.error('Error fetching jobs:', err);
            setError('Error al cargar los jobs');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchJobs();
    }, []);

    // Auto-refresh every 10s if there are running/pending jobs
    useEffect(() => {
        const hasActive = jobs.some(j => j.status === 'running' || j.status === 'pending');
        if (!hasActive) return;
        const interval = setInterval(fetchJobs, 10000);
        return () => clearInterval(interval);
    }, [jobs, fetchJobs]);

    const handleDelete = async (job: VegetationJob) => {
        if (!confirm('Eliminar este job?')) return;
        setDeleting(job.id);
        try {
            await api.deleteJob(job.id);
            setJobs(prev => prev.filter(j => j.id !== job.id));
        } catch (err: any) {
            const msg = err?.response?.data?.detail || 'Error al eliminar';
            alert(msg);
        } finally {
            setDeleting(null);
        }
    };

    const handleBulkDelete = async () => {
        const stuckOrFailed = jobs.filter(j =>
            j.status === 'failed' || j.status === 'cancelled' ||
            (j.status === 'running' || j.status === 'pending')
        );
        if (stuckOrFailed.length === 0) return;
        if (!confirm(`Eliminar ${stuckOrFailed.length} jobs (fallidos + stuck)?`)) return;

        setBulkDeleting(true);
        try {
            // Delete one by one (backend DELETE /jobs deletes failed+stuck)
            for (const job of stuckOrFailed) {
                try {
                    await api.deleteJob(job.id);
                } catch {
                    // Some may fail (running < 1h), ignore
                }
            }
            await fetchJobs();
        } finally {
            setBulkDeleting(false);
        }
    };

    const statusIcon = (status: string) => {
        switch (status) {
            case 'completed': return <CheckCircle className="w-4 h-4 text-emerald-500" />;
            case 'failed': return <XCircle className="w-4 h-4 text-red-500" />;
            case 'running': return <Play className="w-4 h-4 text-blue-500 animate-pulse" />;
            case 'pending': return <Clock className="w-4 h-4 text-amber-500" />;
            case 'cancelled': return <XCircle className="w-4 h-4 text-slate-400" />;
            default: return <AlertCircle className="w-4 h-4 text-slate-400" />;
        }
    };

    const statusLabel = (status: string) => {
        switch (status) {
            case 'completed': return 'Completado';
            case 'failed': return 'Fallido';
            case 'running': return 'Ejecutando';
            case 'pending': return 'Pendiente';
            case 'cancelled': return 'Cancelado';
            default: return status;
        }
    };

    const formatDate = (dateStr: string | null | undefined) => {
        if (!dateStr) return '-';
        try {
            return new Date(dateStr).toLocaleString('es-ES', {
                day: '2-digit', month: 'short', year: 'numeric',
                hour: '2-digit', minute: '2-digit'
            });
        } catch { return dateStr; }
    };

    const stuckCount = jobs.filter(j => j.status === 'running' || j.status === 'pending' || j.status === 'failed').length;

    if (loading && jobs.length === 0) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 className="w-8 h-8 animate-spin text-green-600" />
            </div>
        );
    }

    return (
        <div className="p-6 max-w-5xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
                <div>
                    <h1 className="text-xl font-bold text-slate-900">Jobs</h1>
                    <p className="text-sm text-slate-500">{jobs.length} jobs total</p>
                </div>
                <div className="flex items-center gap-2">
                    {stuckCount > 0 && (
                        <button
                            onClick={handleBulkDelete}
                            disabled={bulkDeleting}
                            className="inline-flex items-center gap-2 px-3 py-2 bg-red-50 text-red-700 hover:bg-red-100 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                        >
                            {bulkDeleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                            Limpiar {stuckCount} stuck/fallidos
                        </button>
                    )}
                    <button
                        onClick={fetchJobs}
                        disabled={loading}
                        className="p-2 text-slate-500 hover:text-green-600 hover:bg-slate-100 rounded-lg transition-colors"
                        title="Actualizar"
                    >
                        <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    </button>
                </div>
            </div>

            {error && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                    {error}
                </div>
            )}

            {/* Jobs Table */}
            {jobs.length === 0 ? (
                <div className="bg-white rounded-xl border border-slate-200 p-12 text-center">
                    <CheckCircle className="w-12 h-12 text-slate-300 mx-auto mb-3" />
                    <h3 className="text-lg font-medium text-slate-700 mb-1">Sin jobs</h3>
                    <p className="text-slate-500 text-sm">No hay jobs pendientes ni anteriores.</p>
                </div>
            ) : (
                <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
                    <table className="w-full text-sm">
                        <thead className="bg-slate-50 border-b border-slate-200">
                            <tr>
                                <th className="text-left py-3 px-4 font-medium text-slate-700">Estado</th>
                                <th className="text-left py-3 px-4 font-medium text-slate-700">Tipo</th>
                                <th className="text-left py-3 px-4 font-medium text-slate-700">Entidad</th>
                                <th className="text-left py-3 px-4 font-medium text-slate-700">Creado</th>
                                <th className="text-left py-3 px-4 font-medium text-slate-700">Error</th>
                                <th className="text-right py-3 px-4 font-medium text-slate-700">Accion</th>
                            </tr>
                        </thead>
                        <tbody>
                            {jobs.map(job => (
                                <tr key={job.id} className="border-b border-slate-100 hover:bg-slate-50/50">
                                    <td className="py-2.5 px-4">
                                        <div className="flex items-center gap-2">
                                            {statusIcon(job.status)}
                                            <span className="text-slate-700">{statusLabel(job.status)}</span>
                                        </div>
                                    </td>
                                    <td className="py-2.5 px-4 text-slate-600">
                                        {job.job_type === 'download' ? 'Descarga' :
                                         job.job_type === 'calculate_index' ? 'Calculo' :
                                         job.job_type}
                                    </td>
                                    <td className="py-2.5 px-4 text-slate-600 max-w-[200px] truncate" title={job.entity_id || ''}>
                                        {job.entity_id
                                            ? job.entity_id.split(':').pop() || job.entity_id
                                            : '-'}
                                    </td>
                                    <td className="py-2.5 px-4 text-slate-500 text-xs">
                                        {formatDate(job.created_at)}
                                    </td>
                                    <td className="py-2.5 px-4 text-red-600 text-xs max-w-[200px] truncate" title={job.error_message || ''}>
                                        {job.error_message || '-'}
                                    </td>
                                    <td className="py-2.5 px-4 text-right">
                                        <button
                                            onClick={() => handleDelete(job)}
                                            disabled={deleting === job.id}
                                            className="p-1.5 text-slate-400 hover:text-red-600 rounded transition-colors disabled:opacity-50"
                                            title="Eliminar"
                                        >
                                            {deleting === job.id
                                                ? <Loader2 className="w-4 h-4 animate-spin" />
                                                : <Trash2 className="w-4 h-4" />
                                            }
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
