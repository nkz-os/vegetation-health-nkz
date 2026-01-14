/**
 * Simple Input component (ui-kit doesn't export Input)
 * Uses Tailwind CSS for styling consistent with platform
 */
import React from 'react';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  // All standard input props are inherited
}

export const Input: React.FC<InputProps> = ({ className = '', ...props }) => {
  return (
    <input
      className={`px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-green-500 focus:border-green-500 ${className}`}
      {...props}
    />
  );
};

















