/**
 * Wrapped VegetationAnalytics export for Module Federation
 * Host does NOT use moduleProvider, so we wrap here
 */
import React from 'react';
import { VegetationAnalytics as BaseComponent } from '../components/VegetationAnalytics';
import { VegetationProvider } from '../services/vegetationContext';

export const VegetationAnalytics: React.FC<any> = (props) => (
  <VegetationProvider>
    <BaseComponent {...props} />
  </VegetationProvider>
);

export default VegetationAnalytics;
