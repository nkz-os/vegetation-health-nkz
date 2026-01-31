/**
 * CollapsiblePanel - A wrapper that makes any panel collapsible
 * Used in the unified viewer to allow sharing space with other modules
 */

import React, { useState } from 'react';
import { ChevronLeft, ChevronRight, Leaf } from 'lucide-react';

interface CollapsiblePanelProps {
    title: string;
    children: React.ReactNode;
    defaultCollapsed?: boolean;
    side?: 'left' | 'right';
    className?: string;
}

export const CollapsiblePanel: React.FC<CollapsiblePanelProps> = ({
    title,
    children,
    defaultCollapsed = false,
    side = 'right',
    className = '',
}) => {
    const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed);

    const ChevronIcon = side === 'right'
        ? (isCollapsed ? ChevronLeft : ChevronRight)
        : (isCollapsed ? ChevronRight : ChevronLeft);

    return (
        <div
            className={`
        relative transition-all duration-300 ease-in-out
        ${isCollapsed ? 'w-12' : 'w-full'}
        ${className}
      `}
        >
            {/* Collapse Toggle Button */}
            <button
                onClick={() => setIsCollapsed(!isCollapsed)}
                className={`
          absolute top-2 z-10 p-1.5 rounded-lg
          bg-white border border-slate-200 shadow-sm
          hover:bg-slate-50 transition-colors
          ${side === 'right' ? '-left-3' : '-right-3'}
        `}
                title={isCollapsed ? 'Expandir panel' : 'Contraer panel'}
            >
                <ChevronIcon className="w-4 h-4 text-slate-600" />
            </button>

            {/* Collapsed State - Show only icon */}
            {isCollapsed ? (
                <div
                    className="h-full flex flex-col items-center pt-12 bg-white border-l border-slate-200 cursor-pointer hover:bg-slate-50"
                    onClick={() => setIsCollapsed(false)}
                >
                    <div className="p-2 bg-green-100 rounded-lg mb-2">
                        <Leaf className="w-5 h-5 text-green-700" />
                    </div>
                    <span
                        className="text-xs font-medium text-slate-600 writing-mode-vertical"
                        style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
                    >
                        {title}
                    </span>
                </div>
            ) : (
                /* Expanded State - Show full content */
                <div className="h-full overflow-hidden">
                    {children}
                </div>
            )}
        </div>
    );
};

export default CollapsiblePanel;
