/**
 * Comparison Slider - A/B comparison tool for vegetation indices.
 * Allows comparing two dates side-by-side with a draggable divider.
 */

import React, { useState, useRef, useEffect } from 'react';
import { GripVertical } from 'lucide-react';

interface ComparisonSliderProps {
  leftImage: string | null;
  rightImage: string | null;
  leftLabel: string;
  rightLabel: string;
  className?: string;
}

export const ComparisonSlider: React.FC<ComparisonSliderProps> = ({
  leftImage,
  rightImage,
  leftLabel,
  rightLabel,
  className = '',
}) => {
  const [sliderPosition, setSliderPosition] = useState(50);
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = () => {
    setIsDragging(true);
  };

  const handleMouseMove = (e: MouseEvent) => {
    if (!isDragging || !containerRef.current) return;

    const rect = containerRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const percentage = (x / rect.width) * 100;
    const clamped = Math.max(10, Math.min(90, percentage));
    setSliderPosition(clamped);
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  useEffect(() => {
    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      return () => {
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
      };
    }
  }, [isDragging]);

  if (!leftImage && !rightImage) {
    return (
      <div className={`bg-gray-100 rounded-lg p-8 text-center text-gray-500 ${className}`}>
        <p>No images available for comparison</p>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={`relative w-full h-96 bg-gray-200 rounded-lg overflow-hidden ${className}`}
    >
      {/* Left Image */}
      {leftImage && (
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{
            backgroundImage: `url(${leftImage})`,
            clipPath: `inset(0 ${100 - sliderPosition}% 0 0)`,
          }}
        >
          <div className="absolute top-4 left-4 bg-black bg-opacity-50 text-white px-3 py-1 rounded text-sm font-semibold">
            {leftLabel}
          </div>
        </div>
      )}

      {/* Right Image */}
      {rightImage && (
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{
            backgroundImage: `url(${rightImage})`,
            clipPath: `inset(0 0 0 ${sliderPosition}%)`,
          }}
        >
          <div className="absolute top-4 right-4 bg-black bg-opacity-50 text-white px-3 py-1 rounded text-sm font-semibold">
            {rightLabel}
          </div>
        </div>
      )}

      {/* Slider Handle */}
      <div
        className="absolute top-0 bottom-0 w-1 bg-white cursor-col-resize z-10 shadow-lg"
        style={{ left: `${sliderPosition}%` }}
        onMouseDown={handleMouseDown}
      >
        <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 bg-white rounded-full p-2 shadow-lg border-2 border-gray-300">
          <GripVertical className="w-5 h-5 text-gray-600" />
        </div>
      </div>

      {/* Instructions */}
      <div className="absolute bottom-4 left-1/2 transform -translate-x-1/2 bg-black bg-opacity-50 text-white px-4 py-2 rounded text-xs">
        Arrastra para comparar
      </div>
    </div>
  );
};














