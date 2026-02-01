import React, { useState, useEffect, lazy, Suspense } from 'react';
import { Card } from '@nekazari/ui-kit';
import { VegetationProvider, useVegetationContext } from './services/vegetationContext';
import { VegetationConfig } from './components/VegetationConfig';
import { VegetationAnalytics } from './components/VegetationAnalytics';
import { CalculationsPage } from './components/pages/CalculationsPage';
import { useVegetationApi } from './services/api';
import { Calendar, Layers, Leaf, ChevronRight, BarChart3, FileDown, Bell, Cloud, MapPin } from 'lucide-react';

// Lazy load new tabs for code splitting
const PrescriptionTab = lazy(() => import('./components/pages/PrescriptionTab'));
const AlertsTab = lazy(() => import('./components/pages/AlertsTab'));
const WeatherTab = lazy(() => import('./components/pages/WeatherTab'));
const ZoningTab = lazy(() => import('./components/pages/ZoningTab'));

// Tab types for the Ferrari frontend
type TabType = 'dashboard' | 'analytics' | 'config' | 'calculations' | 'prescription' | 'alerts' | 'weather' | 'zoning';

// Loading fallback for lazy loaded tabs
const TabLoadingFallback: React.FC = () => (
  <div className="flex items-center justify-center py-12">
    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-600"></div>
    <span className="ml-3 text-slate-500">Cargando...</span>
  </div>
);

/**
 * Read URL search params for deep linking
 * Format: /vegetation?entityId=xxx&tab=analytics
 */
function useDeepLinkParams(): { entityId: string | null; tab: TabType | null } {
  const [params, setParams] = useState<{ entityId: string | null; tab: TabType | null }>({
    entityId: null,
    tab: null
  });

  useEffect(() => {
    const searchParams = new URLSearchParams(window.location.search);
    const entityId = searchParams.get('entityId');
    const tab = searchParams.get('tab') as TabType | null;
    
    // Validate tab is a known type
    const validTabs: TabType[] = ['dashboard', 'analytics', 'config', 'calculations', 'prescription', 'alerts', 'weather', 'zoning'];
    const validatedTab = tab && validTabs.includes(tab) ? tab : null;
    
    setParams({ entityId, tab: validatedTab });
  }, []);

  return params;
}

const DashboardContent: React.FC = () => {
  const {
    selectedEntityId,
    setSelectedEntityId,
  } = useVegetationContext();

  const api = useVegetationApi();
  const [parcels, setParcels] = useState<any[]>([]);
  const [loadingContext, setLoadingContext] = useState(true);
  const [activeTab, setActiveTab] = useState<TabType>('dashboard');
  
  // Deep linking support
  const deepLinkParams = useDeepLinkParams();
  const [deepLinkApplied, setDeepLinkApplied] = useState(false);

  // Fetch parcels - wait for auth context to be available
  useEffect(() => {
    let cancelled = false;
    let retryCount = 0;
    const maxRetries = 5;

    const fetchParcels = async () => {
      // Wait for host auth context to be available
      const hostAuth = (window as any).__nekazariAuthContext;
      if (!hostAuth || !hostAuth.getToken?.()) {
        if (retryCount < maxRetries) {
          retryCount++;
          console.log(`[Vegetation] Waiting for auth context... (${retryCount}/${maxRetries})`);
          setTimeout(fetchParcels, 500);
          return;
        }
        console.warn('[Vegetation] Auth context not available after retries');
      }

      if (cancelled) return;

      try {
        const data = await api.listTenantParcels();
        if (!cancelled) {
          setParcels(data);
          console.log(`[Vegetation] Loaded ${data.length} parcels`);
        }
      } catch (error) {
        console.error('[Vegetation] Error fetching parcels:', error);
      } finally {
        if (!cancelled) {
          setLoadingContext(false);
        }
      }
    };

    setLoadingContext(true);
    fetchParcels();

    return () => { cancelled = true; };
  }, []);

  // Apply deep link params on mount (once)
  useEffect(() => {
    if (deepLinkApplied) return;
    
    if (deepLinkParams.entityId) {
      setSelectedEntityId(deepLinkParams.entityId);
      setActiveTab(deepLinkParams.tab || 'analytics');
      setDeepLinkApplied(true);
    }
  }, [deepLinkParams, deepLinkApplied, setSelectedEntityId]);

  // If a parcel is selected and we're on dashboard, auto-switch to analytics
  useEffect(() => {
    if (selectedEntityId && activeTab === 'dashboard' && !deepLinkParams.tab) {
      setActiveTab('analytics');
    }
  }, [selectedEntityId, activeTab, deepLinkParams.tab]);

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
                    {Array.isArray(parcels) && parcels.map((parcel: any) => {
                      // NGSI-LD format: properties are {value: ...} or plain values
                      const parcelName = parcel.name?.value || parcel.name || parcel.id;
                      const cropSpecies = parcel.cropSpecies?.value || parcel.category?.value || 'Sin asignar';
                      const area = parcel.area?.value || parcel.area;

                      return (
                        <tr
                          key={parcel.id}
                          className="hover:bg-slate-50 transition-colors cursor-pointer"
                          onClick={() => setSelectedEntityId(parcel.id)}
                        >
                          <td className="p-4 font-medium text-slate-900 flex items-center gap-3">
                            <div className="p-2 bg-green-100 text-green-700 rounded-lg">
                              <Leaf className="w-4 h-4" />
                            </div>
                            {parcelName}
                          </td>
                          <td className="p-4">
                            {cropSpecies}
                          </td>
                          <td className="p-4">
                            {area ? (Number(area) / 10000).toFixed(2) : '-'} ha
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
                      );
                    })}
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

  // Detail View (Analytics / Config / New Tabs)
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
            {parcels.find((p: any) => p.id === selectedEntityId)?.name?.value || 
             parcels.find((p: any) => p.id === selectedEntityId)?.name || 
             'Análisis detallado'}
          </h2>
        </div>
      </div>

      {/* Tab Bar - Ferrari Frontend (8 tabs) */}
      <div className="bg-white border-b border-gray-200 px-6">
        <div className="flex overflow-x-auto scrollbar-hide -mb-px">
          {/* Analytics Tab */}
          <TabButton 
            active={activeTab === 'analytics'} 
            onClick={() => setActiveTab('analytics')}
            icon={<Layers className="w-4 h-4" />}
            label="Análisis"
          />
          
          {/* Config Tab */}
          <TabButton 
            active={activeTab === 'config'} 
            onClick={() => setActiveTab('config')}
            icon={<Calendar className="w-4 h-4" />}
            label="Configuración"
          />
          
          {/* Calculations Tab */}
          <TabButton 
            active={activeTab === 'calculations'} 
            onClick={() => setActiveTab('calculations')}
            icon={<BarChart3 className="w-4 h-4" />}
            label="Cálculos"
          />
          
          {/* Prescription Tab (NEW) */}
          <TabButton 
            active={activeTab === 'prescription'} 
            onClick={() => setActiveTab('prescription')}
            icon={<FileDown className="w-4 h-4" />}
            label="Prescripción"
          />
          
          {/* Alerts Tab (NEW) */}
          <TabButton 
            active={activeTab === 'alerts'} 
            onClick={() => setActiveTab('alerts')}
            icon={<Bell className="w-4 h-4" />}
            label="Alertas"
          />
          
          {/* Weather Tab (NEW) */}
          <TabButton 
            active={activeTab === 'weather'} 
            onClick={() => setActiveTab('weather')}
            icon={<Cloud className="w-4 h-4" />}
            label="Clima"
          />
          
          {/* Zoning Tab (NEW) */}
          <TabButton 
            active={activeTab === 'zoning'} 
            onClick={() => setActiveTab('zoning')}
            icon={<MapPin className="w-4 h-4" />}
            label="Zonificación"
          />
        </div>
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-auto bg-slate-50">
        <Suspense fallback={<TabLoadingFallback />}>
          {activeTab === 'analytics' && <VegetationAnalytics />}
          {activeTab === 'config' && <VegetationConfig mode="page" />}
          {activeTab === 'calculations' && <CalculationsPage />}
          {activeTab === 'prescription' && <PrescriptionTab />}
          {activeTab === 'alerts' && <AlertsTab />}
          {activeTab === 'weather' && <WeatherTab />}
          {activeTab === 'zoning' && <ZoningTab />}
        </Suspense>
      </div>
    </div>
  );
};

/**
 * Tab Button Component for consistent styling
 */
const TabButton: React.FC<{
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}> = ({ active, onClick, icon, label }) => (
  <button
    onClick={onClick}
    className={`flex items-center gap-2 px-4 py-3 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
      active
        ? 'border-emerald-600 text-emerald-700'
        : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'
    }`}
  >
    {icon}
    <span>{label}</span>
  </button>
);

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
