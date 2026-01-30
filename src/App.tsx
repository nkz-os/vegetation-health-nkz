import React, { useState, useEffect } from 'react';
import { Card } from '@nekazari/ui-kit';
import { VegetationProvider, useVegetationContext } from './services/vegetationContext';
import { VegetationConfig } from './components/VegetationConfig';
import { VegetationAnalytics } from './components/VegetationAnalytics';
import { Calendar, Layers, Leaf, ChevronRight } from 'lucide-react';

const DashboardContent: React.FC = () => {
  const {
    selectedEntityId,
    setSelectedEntityId,


  } = useVegetationContext();
  const parcels: any[] = [];
  const loadingContext = false;

  const [activeTab, setActiveTab] = useState<'dashboard' | 'analytics' | 'config'>('dashboard');

  // If a parcel is selected, auto-switch to analytics (unless user manually navigates back)
  useEffect(() => {
    if (selectedEntityId && activeTab === 'dashboard') {
      setActiveTab('analytics');
    }
  }, [selectedEntityId]);

  const handleBackToDashboard = () => {
    setSelectedEntityId(null);
    setActiveTab('dashboard');
  };

  if (activeTab === 'dashboard') {
    return (
      <div className="p-6 max-w-7xl mx-auto space-y-6">
        <header className="mb-8">
          <h1 className="text-2xl font-bold text-slate-900">Gestión de Cultivos (Vegetation Prime)</h1>
          <p className="text-slate-600">Selecciona una parcela para analizar su salud vegetativa.</p>
        </header>

        {loadingContext ? (
          <div className="text-center py-12">
            <div className="animate-spin w-8 h-8 border-4 border-green-600 border-t-transparent rounded-full mx-auto mb-4"></div>
            <p className="text-slate-500">Cargando parcelas...</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            <Card className="overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm text-slate-600">
                  <thead className="bg-slate-50 text-slate-900 font-semibold border-b border-slate-200">
                    <tr>
                      <th className="p-4">Nombre de Parcela</th>
                      <th className="p-4">Cultivo Detectado</th>
                      <th className="p-4">Área (ha)</th>
                      <th className="p-4 text-right">Acción</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {Array.isArray(parcels) && parcels.map((parcel: any) => (
                      <tr
                        key={parcel.id}
                        className="hover:bg-slate-50 transition-colors cursor-pointer"
                        onClick={() => setSelectedEntityId(parcel.id)}
                      >
                        <td className="p-4 font-medium text-slate-900 flex items-center gap-3">
                          <div className="p-2 bg-green-100 text-green-700 rounded-lg">
                            <Leaf className="w-4 h-4" />
                          </div>
                          {parcel.name || parcel.id}
                        </td>
                        <td className="p-4">
                          {parcel.cropSpecies?.value || "Sin asignar"}
                        </td>
                        <td className="p-4">
                          {parcel.area ? (parcel.area / 10000).toFixed(2) : '-'} ha
                        </td>
                        <td className="p-4 text-right">
                          <button
                            className="text-white bg-green-600 hover:bg-green-700 px-3 py-1.5 rounded-md text-xs font-medium inline-flex items-center gap-1 transition-colors"
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedEntityId(parcel.id);
                            }}
                          >
                            Analizar <ChevronRight className="w-3 h-3" />
                          </button>
                        </td>
                      </tr>
                    ))}
                    {parcels.length === 0 && (
                      <tr>
                        <td colSpan={4} className="p-8 text-center text-slate-400">
                          No se encontraron parcelas asociadas a tu cuenta.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}
      </div>
    );
  }

  // Detail View (Analytics / Config)
  return (
    <div className="h-full flex flex-col">
      {/* Navigation Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={handleBackToDashboard}
            className="text-slate-500 hover:text-slate-900 font-medium text-sm flex items-center gap-1"
          >
            ← Volver al listado
          </button>
          <div className="h-4 w-px bg-gray-300"></div>
          <h2 className="font-semibold text-slate-800">
            {parcels.find((p: any) => p.id === selectedEntityId)?.name || (activeTab === 'analytics' ? 'Análisis detallado' : 'Configuración')}
          </h2>
        </div>

        <div className="flex bg-slate-100 p-1 rounded-lg">
          <button
            onClick={() => setActiveTab('analytics')}
            className={`px-3 py-1.5 text-sm font-medium rounded-md transition-all ${activeTab === 'analytics'
                ? 'bg-white text-green-700 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
              }`}
          >
            <div className="flex items-center gap-2">
              <Layers className="w-4 h-4" />
              <span>Análisis</span>
            </div>
          </button>
          <button
            onClick={() => setActiveTab('config')}
            className={`px-3 py-1.5 text-sm font-medium rounded-md transition-all ${activeTab === 'config'
                ? 'bg-white text-green-700 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
              }`}
          >
            <div className="flex items-center gap-2">
              <Calendar className="w-4 h-4" />
              <span>Configuración</span>
            </div>
          </button>
        </div>
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-auto bg-slate-50">
        {activeTab === 'analytics' ? (
          <VegetationAnalytics />
        ) : (
          <VegetationConfig mode="page" />
        )}
      </div>
    </div>
  );
};

// Main App Entry Point
const App: React.FC = () => {
  return (
    <VegetationProvider>
      {/* NOTE: Do NOT use min-h-screen here - Host Layout provides page structure */}
      <div className="bg-slate-50 text-slate-900 font-sans">
        <DashboardContent />
      </div>
    </VegetationProvider>
  );
};

export default App;
