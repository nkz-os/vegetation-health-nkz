import React, { useState, useEffect, useMemo } from 'react';

// Define the shape of our UI Kit (fallback interface)
interface UIKit {
  Card: any;
  Button: any;
  Input?: any;
  Select?: any;
  Badge?: any;
}

// CRITICAL: Define fallback components OUTSIDE the hook to prevent re-creation
const FallbackCard = ({ children, className, padding }: any) => {
  const paddingClass = padding === 'sm' ? 'p-2' : padding === 'lg' ? 'p-6' : 'p-4';
  return (
    <div className={`border border-gray-200 rounded-lg bg-white ${paddingClass} ${className || ''}`}>
      {children}
    </div>
  );
};

const FallbackButton = ({ children, variant, size, disabled, onClick, className, ...props }: any) => {
  const sizeClass = size === 'sm' ? 'px-3 py-1.5 text-sm' : size === 'lg' ? 'px-6 py-3 text-base' : 'px-4 py-2 text-sm';
  const variantClass = variant === 'primary' 
    ? 'bg-blue-600 text-white hover:bg-blue-700' 
    : variant === 'danger'
    ? 'bg-red-600 text-white hover:bg-red-700'
    : variant === 'ghost'
    ? 'bg-transparent text-gray-700 hover:bg-gray-100'
    : 'bg-gray-200 text-gray-900 hover:bg-gray-300';
  
  return (
    <button
      {...props}
      onClick={onClick}
      disabled={disabled}
      className={`${sizeClass} ${variantClass} rounded-md font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${className || ''}`}
    >
      {children}
    </button>
  );
};

const FallbackInput = (props: any) => (
  <input
    {...props}
    className={`border border-nkz-border rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 ${props.className || ''}`}
  />
);

const FallbackSelect = (props: any) => (
  <select
    {...props}
    className={`border border-nkz-border rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 ${props.className || ''}`}
  />
);

const FallbackBadge = ({ children, variant, className }: any) => {
  const colorClass = variant === 'success' ? 'bg-green-100 text-green-800' :
                     variant === 'error' ? 'bg-red-100 text-red-800' :
                     'bg-nkz-warning-soft text-yellow-800';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colorClass} ${className || ''}`}>
      {children}
    </span>
  );
};

// Static fallback object (never changes reference)
const FALLBACK_UI_KIT = {
  Card: FallbackCard,
  Button: FallbackButton,
  Input: FallbackInput,
  Select: FallbackSelect,
  Badge: FallbackBadge,
};

/**
 * Safe hook to access UI Kit components from window.__nekazariUIKit
 * 
 * This hook handles the async nature of Host initialization by:
 * 1. Checking immediately if UIKit is available
 * 2. Polling with interval (50ms) with max timeout (5s)
 * 3. Providing fallback components if not loaded
 * 
 * CRITICAL: Fallbacks are defined outside hook to prevent infinite re-renders
 */
export function useUIKit() {
  const [uiKit, setUiKit] = useState<UIKit | null>(null);

  useEffect(() => {
    // Helper to find the global UIKit
    const getGlobal = () => (window as any).__nekazariUIKit;

    // 1. Immediate check
    if (getGlobal()) {
      setUiKit(getGlobal());
      return;
    }

    // 2. Polling Safety Net (Wait for Host to finish hydration)
    const startTime = Date.now();
    const maxWaitTime = 5000; // 5 seconds max
    const pollInterval = 50; // Check every 50ms

    const interval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      
      if (getGlobal()) {
        setUiKit(getGlobal());
        clearInterval(interval);
      } else if (elapsed >= maxWaitTime) {
        // Timeout reached, stop polling
        console.warn('[useUIKit] Timeout: window.__nekazariUIKit not available after 5s. Using fallbacks.');
        clearInterval(interval);
      }
    }, pollInterval);

    // Cleanup
    return () => clearInterval(interval);
  }, []);

  // Return memoized result to prevent unnecessary re-renders
  return useMemo(() => {
    if (!uiKit) {
      return FALLBACK_UI_KIT;
    }

    // Return the Real UI Kit once loaded with fallbacks for missing components
    return {
      ...uiKit,
      Input: uiKit.Input || FallbackInput,
      Select: uiKit.Select || FallbackSelect,
      Badge: uiKit.Badge || FallbackBadge,
    };
  }, [uiKit]);
}
