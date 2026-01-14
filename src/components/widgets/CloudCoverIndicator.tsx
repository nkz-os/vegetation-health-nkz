/**
 * Cloud Cover Indicator - Visual indicator for cloud coverage in satellite scenes.
 * Critical for AgTech: prevents misinterpreting clouds as crop problems.
 */

import React from 'react';
import { Cloud, CloudOff, AlertTriangle } from 'lucide-react';

interface CloudCoverIndicatorProps {
  cloudCoverage?: number | null;
  threshold?: number;
  showWarning?: boolean;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export const CloudCoverIndicator: React.FC<CloudCoverIndicatorProps> = ({
  cloudCoverage,
  threshold = 20,
  showWarning = true,
  size = 'md',
  className = '',
}) => {
  if (cloudCoverage === undefined || cloudCoverage === null) {
    return (
      <div className={`flex items-center gap-1 text-gray-400 ${className}`}>
        <CloudOff className={`${size === 'sm' ? 'w-3 h-3' : size === 'lg' ? 'w-5 h-5' : 'w-4 h-4'}`} />
        <span className={`${size === 'sm' ? 'text-xs' : size === 'lg' ? 'text-sm' : 'text-xs'}`}>
          N/A
        </span>
      </div>
    );
  }

  const isHigh = cloudCoverage > threshold;
  const isMedium = cloudCoverage > threshold * 0.5;

  const sizeClasses = {
    sm: { icon: 'w-3 h-3', text: 'text-xs' },
    md: { icon: 'w-4 h-4', text: 'text-xs' },
    lg: { icon: 'w-5 h-5', text: 'text-sm' },
  };

  const colorClasses = isHigh
    ? 'text-red-600'
    : isMedium
    ? 'text-yellow-600'
    : 'text-green-600';

  return (
    <div className={`flex items-center gap-1 ${colorClasses} ${className}`}>
      {isHigh && showWarning ? (
        <AlertTriangle className={sizeClasses[size].icon} />
      ) : (
        <Cloud className={sizeClasses[size].icon} />
      )}
      <span className={sizeClasses[size].text}>
        {cloudCoverage.toFixed(1)}%
      </span>
      {isHigh && showWarning && (
        <span className={`${sizeClasses[size].text} ml-1`} title="Alta cobertura de nubes - puede afectar la precisión">
          ⚠️
        </span>
      )}
    </div>
  );
};

/**
 * Cloud Cover Badge - Compact badge for displaying cloud coverage in lists/cards
 */
export const CloudCoverBadge: React.FC<{
  cloudCoverage?: number | null;
  threshold?: number;
}> = ({ cloudCoverage, threshold = 20 }) => {
  if (cloudCoverage === undefined || cloudCoverage === null) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600">
        <CloudOff className="w-3 h-3 mr-1" />
        N/A
      </span>
    );
  }

  const isHigh = cloudCoverage > threshold;
  const isMedium = cloudCoverage > threshold * 0.5;

  const badgeClasses = isHigh
    ? 'bg-red-100 text-red-800'
    : isMedium
    ? 'bg-yellow-100 text-yellow-800'
    : 'bg-green-100 text-green-800';

  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${badgeClasses}`}>
      <Cloud className="w-3 h-3 mr-1" />
      {cloudCoverage.toFixed(1)}%
      {isHigh && <AlertTriangle className="w-3 h-3 ml-1" />}
    </span>
  );
};














