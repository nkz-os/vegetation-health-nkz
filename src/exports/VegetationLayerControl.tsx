/**
 * Wrapped VegetationLayerControl export for Module Federation
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
