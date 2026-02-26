import React from 'react';
import { Calendar, Cloud } from 'lucide-react';
import { VegetationScene } from '../../types';

interface DateSelectorProps {
  selectedSceneId?: string | null;
  scenes: VegetationScene[];
  onSelect: (sceneId: string) => void;
}

export const DateSelector: React.FC<DateSelectorProps> = ({ selectedSceneId, scenes, onSelect }) => {
  return (
    <div className="space-y-3">
      <div className="relative">
        <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
          <Calendar className="h-4 w-4 text-slate-400" />
        </div>
        <select
          value={selectedSceneId || ''}
          onChange={(e) => {
            const sceneId = e.target.value;
            if (sceneId) {
              onSelect(sceneId);
            }
          }}
          className="block w-full pl-10 pr-3 py-2 text-sm border border-slate-200 rounded-lg focus:ring-green-500 focus:border-green-500 bg-white text-slate-700 appearance-none cursor-pointer hover:bg-slate-50 transition-colors"
        >
          <option value="">Selecciona una fecha...</option>
          {scenes.map((scene) => (
            <option key={scene.id} value={scene.id}>
              {scene.sensing_date} • {scene.cloud_coverage?.toFixed(0)}% nubes
            </option>
          ))}
        </select>
      </div>
      
      {/* Scene info cards */}
      {scenes.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {scenes.slice(0, 9).map((scene) => {
            const cloudCov = scene.cloud_coverage || 0;
            const cloudColor = cloudCov < 20 ? 'text-green-600' : cloudCov < 50 ? 'text-amber-600' : 'text-red-600';
            const isSelected = selectedSceneId === scene.id;
            
            return (
              <button
                key={scene.id}
                onClick={() => onSelect(scene.id)}
                className={`p-2 rounded-lg border text-xs transition-all ${
                  isSelected
                    ? 'bg-green-50 border-green-300 ring-2 ring-green-500'
                    : 'bg-white border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                }`}
              >
                <div className="font-medium text-slate-700">{scene.sensing_date}</div>
                <div className={`flex items-center gap-1 mt-1 ${cloudColor}`}>
                  <Cloud className="w-3 h-3" />
                  <span>{cloudCov.toFixed(0)}%</span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default DateSelector;
