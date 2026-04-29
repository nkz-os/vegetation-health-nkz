/**
 * CalculationCard — DEPRECATED.
 *
 * Replaced by the flat table in VegetationAnalytics (Task 2.4 refactor).
 * The card grid with mini histograms is no longer used.
 *
 * Kept as a re-export stub in case external slot consumers reference it.
 * Remove entirely after verifying no consumers remain.
 */

import React from 'react';
import { VegetationJob } from '../../types';

interface CalculationCardProps {
  job: VegetationJob;
  onViewInMap?: (job: VegetationJob) => void;
  onDownload?: (job: VegetationJob, format: 'geotiff' | 'png' | 'csv') => void;
  onDelete?: (job: VegetationJob) => void;
}

export const CalculationCard: React.FC<CalculationCardProps> = () => {
  return null;
};

export default CalculationCard;
