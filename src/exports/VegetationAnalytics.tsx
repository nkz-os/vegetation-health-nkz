/**
 * Wrapped VegetationAnalytics export for Module Federation
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
