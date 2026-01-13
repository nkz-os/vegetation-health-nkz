/**
 * Wrapped TimelineWidget export for Module Federation
 * Host does NOT use moduleProvider, so we wrap here
 */
import React from 'react';
import { TimelineWidget as BaseComponent } from '../components/slots/TimelineWidget';
import { VegetationProvider } from '../services/vegetationContext';

export const TimelineWidget: React.FC<any> = (props) => (
  <VegetationProvider>
    <BaseComponent {...props} />
  </VegetationProvider>
);

export default TimelineWidget;
