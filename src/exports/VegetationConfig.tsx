/**
 * Wrapped VegetationConfig export for Module Federation
 * Host does NOT use moduleProvider, so we wrap here
 */
import React from 'react';
import { VegetationConfig as BaseComponent } from '../components/VegetationConfig';
import { VegetationProvider } from '../services/vegetationContext';

export const VegetationConfig: React.FC<any> = (props) => (
  <VegetationProvider>
    <BaseComponent {...props} />
  </VegetationProvider>
);

export default VegetationConfig;
