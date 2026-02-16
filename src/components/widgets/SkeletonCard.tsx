/**
 * SkeletonCard — Reusable shimmer loading component.
 * Provides unified loading states across the module.
 *
 * Variants:
 *   • chart   — Large rectangle placeholder (for timeseries/graphs)
 *   • stats   — 4-column grid of small stat boxes
 *   • table   — Repeating horizontal lines (for tables/lists)
 */

import React from 'react';

type SkeletonVariant = 'chart' | 'stats' | 'table';

interface SkeletonCardProps {
    variant?: SkeletonVariant;
    className?: string;
    /** Number of rows for table variant */
    rows?: number;
}

const shimmer = 'animate-pulse bg-slate-200/80 rounded';

const ChartSkeleton: React.FC = () => (
    <div className="space-y-3">
        <div className="flex items-center justify-between">
            <div className={`${shimmer} h-5 w-40`} />
            <div className={`${shimmer} h-4 w-24`} />
        </div>
        <div className={`${shimmer} h-48 w-full rounded-lg`} />
        <div className="flex gap-4">
            <div className={`${shimmer} h-3 w-16`} />
            <div className={`${shimmer} h-3 w-20`} />
            <div className={`${shimmer} h-3 w-24`} />
        </div>
    </div>
);

const StatsSkeleton: React.FC = () => (
    <div className="space-y-3">
        <div className={`${shimmer} h-5 w-36`} />
        <div className="grid grid-cols-2 gap-4">
            {[0, 1, 2, 3].map((i) => (
                <div key={i} className="p-3 bg-slate-50 rounded-lg border border-slate-100 space-y-2">
                    <div className={`${shimmer} h-3 w-16`} />
                    <div className={`${shimmer} h-7 w-20`} />
                </div>
            ))}
        </div>
    </div>
);

const TableSkeleton: React.FC<{ rows: number }> = ({ rows }) => (
    <div className="space-y-3">
        <div className={`${shimmer} h-5 w-36`} />
        <div className="space-y-2">
            {/* Header row */}
            <div className="flex gap-4">
                <div className={`${shimmer} h-3 w-16`} />
                <div className={`${shimmer} h-3 w-20`} />
                <div className={`${shimmer} h-3 w-12`} />
            </div>
            {/* Data rows */}
            {Array.from({ length: rows }).map((_, i) => (
                <div key={i} className="flex gap-4 py-2 border-t border-slate-100">
                    <div className={`${shimmer} h-4 w-16`} />
                    <div className={`${shimmer} h-4 w-20`} />
                    <div className={`${shimmer} h-4 w-12`} />
                </div>
            ))}
        </div>
    </div>
);

export const SkeletonCard: React.FC<SkeletonCardProps> = ({
    variant = 'chart',
    className = '',
    rows = 4,
}) => {
    return (
        <div className={`bg-white/90 backdrop-blur-md rounded-xl border border-slate-200/50 shadow-sm p-6 ${className}`}>
            {variant === 'chart' && <ChartSkeleton />}
            {variant === 'stats' && <StatsSkeleton />}
            {variant === 'table' && <TableSkeleton rows={rows} />}
        </div>
    );
};

export default SkeletonCard;
