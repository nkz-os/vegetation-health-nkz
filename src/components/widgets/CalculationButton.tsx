import React from 'react';
import { Calculator, Loader2, AlertCircle, CheckCircle } from 'lucide-react';
import { useVegetationContext } from '../../services/vegetationContext';
import { useIndexCalculation } from '../../hooks/useIndexCalculation';

interface CalculationButtonProps {
  sceneId?: string;
  entityId?: string;
  indexType?: string;
  variant?: 'primary' | 'secondary';
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  /** Date range (used together); range is for filtering timeline only when not using singleDate. */
  startDate?: string;
  endDate?: string;
  /** Single date to calculate: triggers calculation for this day only (§12.6). */
  singleDate?: string;
  formula?: string;
}

export const CalculationButton: React.FC<CalculationButtonProps> = ({
  sceneId,
  entityId,
  indexType,
  variant = 'primary',
  size = 'md',
  className = '',
  startDate,
  endDate,
  singleDate,
  formula,
}) => {
  const { selectedIndex, selectedSceneId, selectedEntityId, setSelectedIndex } = useVegetationContext();
  const { calculateIndex, isCalculating, error, success, resetState } = useIndexCalculation();

  const effectiveSceneId = sceneId || selectedSceneId;
  const effectiveEntityId = entityId || selectedEntityId;
  const effectiveIndexType = (indexType || selectedIndex) as any;

  const canCalculate = Boolean(
    effectiveEntityId && effectiveIndexType &&
    (effectiveSceneId || singleDate || (startDate && endDate))
  );

  const handleClick = async () => {
    resetState();
    const useStart = singleDate ? singleDate : startDate;
    const useEnd = singleDate ? singleDate : endDate;
    const jobId = await calculateIndex({
      sceneId: effectiveSceneId || undefined,
      entityId: effectiveEntityId || undefined,
      indexType: effectiveIndexType,
      startDate: useStart,
      endDate: useEnd,
      formula: formula,
    });

    if (jobId && setSelectedIndex) {
      console.log('[CalculationButton] Calculation success, refreshing map layer:', effectiveIndexType);
      setSelectedIndex(effectiveIndexType);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <button
        onClick={handleClick}
        disabled={isCalculating || !canCalculate}
        className={`
          px-4 py-2 rounded-md font-medium transition-all
          disabled:opacity-50 disabled:cursor-not-allowed
          flex items-center gap-2 justify-center
          ${variant === 'primary'
            ? 'bg-blue-600 text-white hover:bg-blue-700 active:bg-blue-800'
            : 'bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 active:bg-slate-100'}
          ${size === 'sm' ? 'text-xs px-2 py-1' : size === 'lg' ? 'text-lg px-6 py-3' : 'text-sm'}
          ${className}
        `}
      >
        {isCalculating ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Calculando...</span>
          </>
        ) : (
          <>
            <Calculator className="w-4 h-4" />
            <span>Calcular Índice</span>
          </>
        )}
      </button>

      {error && (
        <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 p-2 rounded">
          <AlertCircle className="w-4 h-4" />
          <span>{error}</span>
        </div>
      )}

      {success && (
        <div className="flex items-center gap-2 text-xs text-green-600 bg-green-50 p-2 rounded">
          <CheckCircle className="w-4 h-4" />
          <span>Índice calculado correctamente</span>
        </div>
      )}
    </div>
  );
};
