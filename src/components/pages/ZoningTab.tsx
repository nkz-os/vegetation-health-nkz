/**
 * Zoning Tab - Ferrari Frontend
 * 
 * Generate VRA zones and see status; link to "View on map" (round-trip navigation).
 * Supports the round-trip workflow from module page to Unified Viewer with correct layers.
 */

import React, { useState, useEffect } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { ZoningGeoJsonResponse } from '../../types';
import { MapPin, RefreshCw, Map, Layers, CheckCircle, AlertCircle, Clock } from 'lucide-react';

// Zone colors for display
const ZONE_COLORS: Record<string, string> = {
  'high': 'bg-emerald-500',
  'medium': 'bg-yellow-500',
  'low': 'bg-red-500',
  'zone_1': 'bg-emerald-500',
  'zone_2': 'bg-lime-500',
  'zone_3': 'bg-yellow-500',
  'zone_4': 'bg-orange-500',
  'zone_5': 'bg-red-500',
};

interface ZoningJob {
  task_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  created_at: string;
}

const ZoningTab: React.FC = () => {
  const { selectedEntityId } = useVegetationContext();
  const api = useVegetationApi();
  
  const [zoningData, setZoningData] = useState<ZoningGeoJsonResponse | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [message, setMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [currentJob, setCurrentJob] = useState<ZoningJob | null>(null);
  
  // Zoning options
  const [numZones, setNumZones] = useState(3);

  // Load existing zoning data on mount
  useEffect(() => {
    if (!selectedEntityId) {
      setIsLoading(false);
      return;
    }

    loadZoningData();
  }, [selectedEntityId]);

  const loadZoningData = async () => {
    if (!selectedEntityId) return;
    
    setIsLoading(true);
    try {
      const data = await api.getZoningGeoJson(selectedEntityId);
      if (data && data.features && data.features.length > 0) {
        setZoningData(data);
      } else {
        setZoningData(null);
      }
    } catch (error) {
      // No existing zoning data - that's okay
      console.debug('No existing zoning data:', error);
      setZoningData(null);
    } finally {
      setIsLoading(false);
    }
  };

  const handleGenerateZones = async () => {
    if (!selectedEntityId) return;
    
    setIsGenerating(true);
    setMessage(null);
    
    try {
      const result = await api.triggerZoning(selectedEntityId, {
        n_zones: numZones
      });
      
      setCurrentJob({
        task_id: result.task_id,
        status: 'running',
        created_at: new Date().toISOString()
      });
      
      setMessage({ 
        type: 'info', 
        text: `Generando ${numZones} zonas. Task ID: ${result.task_id}` 
      });

      // Poll for completion (simple version - in production use WebSocket or better polling)
      pollForCompletion(result.task_id);
    } catch (error) {
      console.error('Zoning trigger failed:', error);
      setMessage({ type: 'error', text: 'Error al iniciar la generación de zonas' });
    } finally {
      setIsGenerating(false);
    }
  };

  const pollForCompletion = async (taskId: string) => {
    // Simple polling - check every 3 seconds for up to 60 seconds
    let attempts = 0;
    const maxAttempts = 20;
    
    const poll = async () => {
      attempts++;
      try {
        const data = await api.getZoningGeoJson(selectedEntityId!);
        if (data && data.features && data.features.length > 0) {
          setZoningData(data);
          setCurrentJob(prev => prev ? { ...prev, status: 'completed' } : null);
          setMessage({ type: 'success', text: `Zonas generadas: ${data.features.length} zonas` });
          return;
        }
      } catch (error) {
        // Still processing
      }
      
      if (attempts < maxAttempts) {
        setTimeout(poll, 3000);
      } else {
        setMessage({ 
          type: 'info', 
          text: 'El proceso está tardando más de lo esperado. Pulsa "Actualizar" para comprobar.' 
        });
      }
    };

    setTimeout(poll, 3000);
  };

  /**
   * Navigate to viewer with vegetation + zoning layers active
   * Round-trip navigation per FRONTEND_FERRARI_PROPOSAL.md
   */
  const handleViewOnMap = () => {
    if (!selectedEntityId) return;
    
    // Format: /entities?selectedEntity={id}&activeLayers=vegetation,zoning
    const url = `/entities?selectedEntity=${encodeURIComponent(selectedEntityId)}&activeLayers=vegetation,zoning`;
    
    if ((window as any).__nekazariNavigate) {
      (window as any).__nekazariNavigate(url);
    } else {
      window.location.href = url;
    }
  };

  if (!selectedEntityId) {
    return (
      <div className="p-6 text-center">
        <MapPin className="w-12 h-12 text-slate-300 mx-auto mb-3" />
        <div className="text-slate-400 text-lg mb-2">Selecciona una parcela</div>
        <p className="text-slate-500 text-sm">
          Vuelve al listado y selecciona una parcela para generar zonas de manejo variable.
        </p>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <header className="mb-6">
        <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <MapPin className="w-6 h-6 text-emerald-600" />
          Zonificación VRA
        </h1>
        <p className="text-slate-600 text-sm mt-1">
          Genera zonas de manejo variable (VRA) basadas en índices de vegetación.
        </p>
      </header>

      {/* Message */}
      {message && (
        <div className={`mb-6 p-4 rounded-lg flex items-start gap-3 ${
          message.type === 'success' ? 'bg-emerald-50 text-emerald-800 border border-emerald-200' :
          message.type === 'error' ? 'bg-red-50 text-red-800 border border-red-200' :
          'bg-blue-50 text-blue-800 border border-blue-200'
        }`}>
          {message.type === 'success' && <CheckCircle className="w-5 h-5 flex-shrink-0" />}
          {message.type === 'error' && <AlertCircle className="w-5 h-5 flex-shrink-0" />}
          {message.type === 'info' && <Clock className="w-5 h-5 flex-shrink-0" />}
          <span>{message.text}</span>
        </div>
      )}

      {/* Generate Zones Section */}
      <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
        <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
          <Layers className="w-5 h-5 text-emerald-600" />
          Generar Zonas
        </h2>

        <div className="space-y-4">
          {/* Number of Zones */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-2">
              Número de Zonas
            </label>
            <div className="flex gap-2">
              {[2, 3, 4, 5].map(n => (
                <button
                  key={n}
                  onClick={() => setNumZones(n)}
                  className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                    numZones === n
                      ? 'bg-emerald-600 text-white'
                      : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
            <p className="text-xs text-slate-500 mt-2">
              Se crearán {numZones} zonas basadas en los valores del índice de vegetación seleccionado.
            </p>
          </div>

          {/* Generate Button */}
          <div className="flex gap-3">
            <button
              onClick={handleGenerateZones}
              disabled={isGenerating}
              className="flex-1 flex items-center justify-center gap-2 py-3 bg-emerald-600 text-white rounded-lg font-medium hover:bg-emerald-700 disabled:bg-slate-300 disabled:cursor-wait transition-colors"
            >
              {isGenerating ? (
                <>
                  <span className="animate-spin rounded-full h-5 w-5 border-2 border-white border-t-transparent"></span>
                  Generando...
                </>
              ) : (
                <>
                  <MapPin className="w-5 h-5" />
                  Generar Zonas VRA
                </>
              )}
            </button>

            <button
              onClick={loadZoningData}
              disabled={isLoading}
              className="px-4 py-3 bg-slate-100 text-slate-700 rounded-lg font-medium hover:bg-slate-200 disabled:opacity-50 transition-colors"
              title="Actualizar datos de zonificación"
            >
              <RefreshCw className={`w-5 h-5 ${isLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>
      </section>

      {/* Existing Zones */}
      {isLoading ? (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-600"></div>
            <span className="ml-3 text-slate-500">Cargando zonas existentes...</span>
          </div>
        </div>
      ) : zoningData && zoningData.features.length > 0 ? (
        <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
          <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
            <CheckCircle className="w-5 h-5 text-emerald-600" />
            Zonas Existentes ({zoningData.features.length})
          </h2>

          <div className="space-y-3 mb-4">
            {zoningData.features.map((feature, idx) => {
              const props = feature.properties;
              const zoneClass = props.zone_class || `zone_${idx + 1}`;
              const colorClass = ZONE_COLORS[zoneClass] || 'bg-slate-400';
              
              return (
                <div 
                  key={idx}
                  className="flex items-center justify-between p-3 bg-slate-50 rounded-lg border border-slate-100"
                >
                  <div className="flex items-center gap-3">
                    <div className={`w-4 h-4 rounded ${colorClass}`}></div>
                    <div>
                      <div className="font-medium text-slate-800">
                        Zona {props.zone_id || idx + 1}
                      </div>
                      <div className="text-xs text-slate-500">
                        {zoneClass.replace('_', ' ').replace(/^\w/, (c: string) => c.toUpperCase())}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="font-semibold text-slate-800">
                      {props.mean_value?.toFixed(3) || '-'}
                    </div>
                    <div className="text-xs text-slate-500">
                      {props.area_ha?.toFixed(2) || '-'} ha
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* View on Map - Round-trip navigation */}
          <button
            onClick={handleViewOnMap}
            className="w-full flex items-center justify-center gap-2 py-3 bg-emerald-50 text-emerald-700 rounded-lg font-medium hover:bg-emerald-100 border border-emerald-200 transition-colors"
          >
            <Map className="w-5 h-5" />
            Ver en el mapa
          </button>
        </section>
      ) : (
        <section className="bg-slate-50 rounded-xl border border-slate-200 p-6 text-center">
          <MapPin className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-500 mb-2">No hay zonas generadas para esta parcela</p>
          <p className="text-slate-400 text-sm">
            Pulsa "Generar Zonas VRA" para crear zonas de manejo variable.
          </p>
        </section>
      )}

      {/* Current Job Status */}
      {currentJob && currentJob.status === 'running' && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
          <div className="flex items-center gap-3">
            <div className="animate-spin rounded-full h-5 w-5 border-2 border-blue-600 border-t-transparent"></div>
            <div>
              <div className="font-medium text-blue-800">Generando zonas...</div>
              <div className="text-sm text-blue-600">Task ID: {currentJob.task_id}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ZoningTab;
