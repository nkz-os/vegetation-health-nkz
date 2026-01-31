/**
 * CalculationCard - Card component for displaying a historical calculation
 * Shows parcel name, index, date, mini histogram, and download button
 */

import React from 'react';
import { Download, MapPin, Calendar, BarChart3, Eye, ChevronDown, Trash2 } from 'lucide-react';
import { VegetationJob } from '../../types';
import { getIndexInfo } from '../../data/indexInfo';

interface CalculationCardProps {
    job: VegetationJob;
    onViewInMap?: (job: VegetationJob) => void;
    onDownload?: (job: VegetationJob, format: 'geotiff' | 'png' | 'csv') => void;
    onDelete?: (job: VegetationJob) => void;
}

export const CalculationCard: React.FC<CalculationCardProps> = ({
    job,
    onViewInMap,
    onDownload,
    onDelete,
}) => {
    const [showDownloadMenu, setShowDownloadMenu] = React.useState(false);
    const indexInfo = getIndexInfo(job.index_type || 'NDVI');

    // Parse histogram data if available (stored in result_url or metadata)
    const histogramData = job.result_histogram || null;

    // Format date
    const formatDate = (dateStr: string | undefined) => {
        if (!dateStr) return 'Fecha no disponible';
        const date = new Date(dateStr);
        return date.toLocaleDateString('es-ES', {
            day: '2-digit',
            month: 'short',
            year: 'numeric'
        });
    };

    // Mini histogram bars (simplified visualization)
    const renderMiniHistogram = () => {
        if (!histogramData || !histogramData.bins || histogramData.bins.length === 0) {
            // Generate a sample gradient if no data
            return (
                <div className="flex items-end gap-0.5 h-8">
                    {[0.2, 0.4, 0.7, 0.9, 1, 0.8, 0.6, 0.3, 0.15].map((h, i) => (
                        <div
                            key={i}
                            className="flex-1 rounded-t-sm transition-all"
                            style={{
                                height: `${h * 100}%`,
                                backgroundColor: indexInfo?.color || '#22c55e',
                                opacity: 0.3 + (i / 10) * 0.7
                            }}
                        />
                    ))}
                </div>
            );
        }

        const maxCount = Math.max(...histogramData.counts);
        return (
            <div className="flex items-end gap-0.5 h-8">
                {histogramData.counts.slice(0, 10).map((count: number, i: number) => (
                    <div
                        key={i}
                        className="flex-1 rounded-t-sm transition-all hover:opacity-80"
                        style={{
                            height: `${(count / maxCount) * 100}%`,
                            backgroundColor: indexInfo?.color || '#22c55e',
                            minHeight: '2px'
                        }}
                    />
                ))}
            </div>
        );
    };

    return (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-shadow overflow-hidden">
            {/* Header with index badge */}
            <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <span
                        className="px-2.5 py-1 rounded-full text-xs font-bold text-white"
                        style={{ backgroundColor: indexInfo?.color || '#22c55e' }}
                    >
                        {job.index_type || 'NDVI'}
                    </span>
                    <span className="text-xs text-slate-500">
                        {job.status === 'completed' ? '✓ Completado' : job.status}
                    </span>
                </div>
                <div className="flex items-center gap-1 text-xs text-slate-400">
                    <Calendar className="w-3 h-3" />
                    {formatDate(job.created_at)}
                </div>
            </div>

            {/* Body */}
            <div className="p-4 space-y-3">
                {/* Entity/Parcel name */}
                <div className="flex items-center gap-2">
                    <MapPin className="w-4 h-4 text-slate-400" />
                    <span className="text-sm font-medium text-slate-800 truncate">
                        {job.entity_name || job.entity_id || 'Zona personalizada'}
                    </span>
                </div>

                {/* Mini Histogram */}
                <div className="bg-slate-50 rounded-lg p-3">
                    <div className="flex items-center gap-1 text-xs text-slate-500 mb-2">
                        <BarChart3 className="w-3 h-3" />
                        <span>Distribución de valores</span>
                    </div>
                    {renderMiniHistogram()}
                    <div className="flex justify-between text-[10px] text-slate-400 mt-1">
                        <span>-1</span>
                        <span>0</span>
                        <span>+1</span>
                    </div>
                </div>

                {/* Stats summary if available */}
                {job.result_stats && (
                    <div className="grid grid-cols-3 gap-2 text-center">
                        <div className="bg-slate-50 rounded-lg p-2">
                            <div className="text-[10px] text-slate-500">Media</div>
                            <div className="text-sm font-semibold text-slate-800">
                                {job.result_stats.mean?.toFixed(3) || '-'}
                            </div>
                        </div>
                        <div className="bg-slate-50 rounded-lg p-2">
                            <div className="text-[10px] text-slate-500">Mín</div>
                            <div className="text-sm font-semibold text-slate-800">
                                {job.result_stats.min?.toFixed(3) || '-'}
                            </div>
                        </div>
                        <div className="bg-slate-50 rounded-lg p-2">
                            <div className="text-[10px] text-slate-500">Máx</div>
                            <div className="text-sm font-semibold text-slate-800">
                                {job.result_stats.max?.toFixed(3) || '-'}
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Footer Actions */}
            <div className="px-4 py-3 bg-slate-50 border-t border-slate-100 flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => onViewInMap?.(job)}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-green-700 bg-green-100 hover:bg-green-200 rounded-lg transition-colors"
                    >
                        <Eye className="w-3.5 h-3.5" />
                        Ver en mapa
                    </button>

                    {/* Delete button */}
                    <button
                        onClick={() => {
                            if (window.confirm('¿Eliminar este cálculo del historial?')) {
                                onDelete?.(job);
                            }
                        }}
                        className="flex items-center gap-1.5 px-2 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                        title="Eliminar cálculo"
                    >
                        <Trash2 className="w-3.5 h-3.5" />
                    </button>
                </div>

                {/* Download dropdown */}
                <div className="relative">
                    <button
                        onClick={() => setShowDownloadMenu(!showDownloadMenu)}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-200 hover:bg-slate-50 rounded-lg transition-colors"
                    >
                        <Download className="w-3.5 h-3.5" />
                        Descargar
                        <ChevronDown className="w-3 h-3" />
                    </button>

                    {showDownloadMenu && (
                        <div
                            className="absolute right-0 bottom-full mb-1 bg-white border border-slate-200 rounded-lg shadow-lg py-1 min-w-[140px] z-10"
                            onMouseLeave={() => setShowDownloadMenu(false)}
                        >
                            <button
                                onClick={() => {
                                    onDownload?.(job, 'geotiff');
                                    setShowDownloadMenu(false);
                                }}
                                className="w-full px-3 py-2 text-left text-xs hover:bg-slate-50 flex items-center gap-2"
                            >
                                <span className="w-8 text-[10px] font-mono bg-slate-100 px-1 rounded">TIFF</span>
                                GeoTIFF (Raster)
                            </button>
                            <button
                                onClick={() => {
                                    onDownload?.(job, 'png');
                                    setShowDownloadMenu(false);
                                }}
                                className="w-full px-3 py-2 text-left text-xs hover:bg-slate-50 flex items-center gap-2"
                            >
                                <span className="w-8 text-[10px] font-mono bg-slate-100 px-1 rounded">PNG</span>
                                Imagen PNG
                            </button>
                            <button
                                onClick={() => {
                                    onDownload?.(job, 'csv');
                                    setShowDownloadMenu(false);
                                }}
                                className="w-full px-3 py-2 text-left text-xs hover:bg-slate-50 flex items-center gap-2"
                            >
                                <span className="w-8 text-[10px] font-mono bg-slate-100 px-1 rounded">CSV</span>
                                Estadísticas CSV
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default CalculationCard;
