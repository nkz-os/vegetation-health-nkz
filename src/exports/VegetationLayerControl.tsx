/**
 * Wrapped VegetationLayerControl export for Module Federation
 * Host does NOT use moduleProvider, so we wrap here
 */
import React from 'react';
import VegetationLayerControl from '../components/slots/VegetationLayerControl';
import { VegetationProvider } from '../services/vegetationContext';

export const WrappedVegetationLayerControl: React.FC<any> = (props) => (
  <VegetationProvider>
    <VegetationLayerControl {...props} />
  </VegetationProvider>
);

export { WrappedVegetationLayerControl as VegetationLayerControl };
export default WrappedVegetationLayerControl;
