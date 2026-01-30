import React from 'react';

interface DateRange {
    startDate: Date | null;
    endDate: Date | null;
}

interface DateRangePickerProps {
    dateRange: DateRange;
    onChange: (range: DateRange) => void;
    presets?: boolean;
}

/**
 * DateRangePicker - Select start and end dates for temporal analysis.
 * Includes quick presets for common time periods.
 */
export const DateRangePicker: React.FC<DateRangePickerProps> = ({
    dateRange,
    onChange,
    presets = true,
}) => {
    const formatDate = (date: Date | null): string => {
        if (!date) return '';
        return date.toISOString().split('T')[0];
    };

    const handleStartChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const newDate = e.target.value ? new Date(e.target.value) : null;
        onChange({ ...dateRange, startDate: newDate });
    };

    const handleEndChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const newDate = e.target.value ? new Date(e.target.value) : null;
        onChange({ ...dateRange, endDate: newDate });
    };

    // Quick presets
    const setPreset = (days: number) => {
        const end = new Date();
        const start = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
        onChange({ startDate: start, endDate: end });
    };

    const setYearPreset = (year: number) => {
        const start = new Date(year, 0, 1); // Jan 1
        const end = new Date(year, 11, 31); // Dec 31
        onChange({ startDate: start, endDate: end });
    };

    const currentYear = new Date().getFullYear();

    return (
        <div className="space-y-3">
            {/* Date inputs */}
            <div className="grid grid-cols-2 gap-2">
                <div>
                    <label className="block text-xs text-slate-500 mb-1">Desde</label>
                    <input
                        type="date"
                        value={formatDate(dateRange.startDate)}
                        onChange={handleStartChange}
                        className="w-full px-2 py-1.5 text-sm border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                </div>
                <div>
                    <label className="block text-xs text-slate-500 mb-1">Hasta</label>
                    <input
                        type="date"
                        value={formatDate(dateRange.endDate)}
                        onChange={handleEndChange}
                        className="w-full px-2 py-1.5 text-sm border border-slate-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                </div>
            </div>

            {/* Quick presets */}
            {presets && (
                <div className="flex flex-wrap gap-1.5">
                    <button
                        type="button"
                        onClick={() => setPreset(30)}
                        className="px-2 py-1 text-xs bg-slate-100 hover:bg-slate-200 rounded-md transition-colors"
                    >
                        30 días
                    </button>
                    <button
                        type="button"
                        onClick={() => setPreset(90)}
                        className="px-2 py-1 text-xs bg-slate-100 hover:bg-slate-200 rounded-md transition-colors"
                    >
                        3 meses
                    </button>
                    <button
                        type="button"
                        onClick={() => setPreset(365)}
                        className="px-2 py-1 text-xs bg-slate-100 hover:bg-slate-200 rounded-md transition-colors"
                    >
                        12 meses
                    </button>
                    <button
                        type="button"
                        onClick={() => setYearPreset(currentYear)}
                        className="px-2 py-1 text-xs bg-blue-100 hover:bg-blue-200 text-blue-700 rounded-md transition-colors"
                    >
                        {currentYear}
                    </button>
                    <button
                        type="button"
                        onClick={() => setYearPreset(currentYear - 1)}
                        className="px-2 py-1 text-xs bg-slate-100 hover:bg-slate-200 rounded-md transition-colors"
                    >
                        {currentYear - 1}
                    </button>
                </div>
            )}

            {/* Show selected range summary */}
            {dateRange.startDate && dateRange.endDate && (
                <p className="text-xs text-slate-500">
                    {Math.ceil((dateRange.endDate.getTime() - dateRange.startDate.getTime()) / (1000 * 60 * 60 * 24))} días seleccionados
                </p>
            )}
        </div>
    );
};

export default DateRangePicker;
