import React, { useState, useEffect } from 'react';
import { Card } from '@nekazari/ui-kit';
import { VegetationProvider, useVegetationContext } from './services/vegetationContext';

import { useTranslation } from '@nekazari/sdk';

// VegetationAnalytics is the legacy detail view; ParcelDetail is the new
// season-centric one wired into the route below. The legacy import stays
// available for the next slices that still need bits of its logic.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import { VegetationAnalytics as _LegacyVegetationAnalytics } from './components/VegetationAnalytics';
import { ParcelDetail } from './components/ParcelDetail';
import { useVegetationApi } from './services/api';
import { Leaf, ChevronRight } from 'lucide-react';

/**
 * Compute area in hectares from a GeoJSON Polygon/MultiPolygon geometry.
 * Uses the Shoelace formula on WGS84 coords with a cos(lat) correction.
 */
function computeAreaHa(geometry: any): number | null {
  if (!geometry?.coordinates) return null;
  try {
    const rings = geometry.type === 'MultiPolygon'
      ? geometry.coordinates.flat()
      : geometry.coordinates;
    const outer = rings[0]; // outer ring [[lng, lat], ...]
    if (!outer || outer.length < 4) return null;

    // Approximate area using Shoelace + cos(lat) scaling
    const toRad = (d: number) => (d * Math.PI) / 180;
    const R = 6371000; // Earth radius in meters
    let area = 0;
    for (let i = 0; i < outer.length - 1; i++) {
      const [x1, y1] = outer[i];
      const [x2, y2] = outer[i + 1];
      area += toRad(x2 - x1) * (2 + Math.sin(toRad(y1)) + Math.sin(toRad(y2)));
    }
    area = Math.abs((area * R * R) / 2);
    return area / 10000; // m² → ha
  } catch {
    return null;
  }
}

// Single view: Dashboard → Analysis (no tabs needed)
type TabType = 'dashboard' | 'analysis';

/**
 * Read URL search params for deep linking
 * Format: /vegetation?entityId=xxx&tab=analysis
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

    const validTabs: TabType[] = ['dashboard', 'analysis'];
    const validatedTab = tab && validTabs.includes(tab) ? tab : null;

    setParams({ entityId, tab: validatedTab });
  }, []);

  return params;
}

const DashboardContent: React.FC = () => {
  const { t } = useTranslation();
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
      if (!hostAuth || !hostAuth.isAuthenticated) {
        if (retryCount < maxRetries) {
          retryCount++;
          setTimeout(fetchParcels, 500);
          return;
        }
      }

      if (cancelled) return;

      try {
        const data = await api.listTenantParcels();
        if (!cancelled) {
          setParcels(data);
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
      setActiveTab(deepLinkParams.tab || 'analysis');
      setDeepLinkApplied(true);
    }
  }, [deepLinkParams, deepLinkApplied, setSelectedEntityId]);

  // If a parcel is selected and we're on dashboard, auto-switch to analytics (unless user opened calculations from dashboard)
  useEffect(() => {
    if (selectedEntityId && activeTab === 'dashboard' && !deepLinkParams.tab) {
      setActiveTab('analysis');
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
          <h1 className="text-2xl font-bold text-slate-900">{t('dashboard.title')}</h1>
          <p className="text-slate-600">{t('dashboard.subtitle')}</p>
        </header>

        {loadingContext ? (
          <div className="text-center py-12">
            <div className="animate-spin w-8 h-8 border-4 border-green-600 border-t-transparent rounded-full mx-auto mb-4"></div>
            <p className="text-slate-500">{t('dashboard.loadingParcels')}</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            <Card className="overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm text-slate-600">
                  <thead className="bg-slate-50 text-slate-900 font-semibold border-b border-slate-200">
                    <tr>
                      <th className="p-4">{t('dashboard.parcelName')}</th>
                      <th className="p-4">{t('dashboard.health')}</th>
                      <th className="p-4">{t('dashboard.detectedCrop')}</th>
                      <th className="p-4">{t('dashboard.area')}</th>
                      <th className="p-4 text-right">{t('dashboard.action')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {Array.isArray(parcels) && parcels.map((parcel: any) => {
                      // NGSI-LD format: properties are {value: ...} or plain values
                      const parcelName = parcel.name?.value || parcel.name || parcel.id;
                      const cropSpecies = parcel.cropSpecies?.value || parcel.category?.value || t('dashboard.unassigned');

                      // Handle area: NGSI-LD `area` attribute is already in hectares
                      let areaHa = '-';
                      const rawArea = parcel.area?.value ?? parcel.area ?? null;

                      if (rawArea !== null && !isNaN(Number(rawArea)) && Number(rawArea) > 0) {
                        areaHa = Number(rawArea).toFixed(2);
                      } else {
                        // Fallback: compute from location geometry
                        const geom = parcel.location?.value || parcel.location;
                        const computed = geom ? computeAreaHa(geom) : null;
                        if (computed !== null && computed > 0) {
                          areaHa = computed.toFixed(2);
                        }
                      }

                      // NDVI health badge: green ≥ 0.6, yellow 0.3-0.6, red < 0.3
                      const latestNdvi = parcel.latestNDVI?.value ?? parcel.vegetationIndex?.value ?? parcel.ndvi?.value ?? null;
                      const healthColor = latestNdvi === null ? 'bg-slate-300' :
                        latestNdvi >= 0.6 ? 'bg-emerald-500' :
                          latestNdvi >= 0.3 ? 'bg-amber-400' : 'bg-red-500';

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
                            <span className={`inline-block w-3 h-3 rounded-full ${healthColor} shadow-sm`}
                              title={latestNdvi !== null ? `NDVI: ${latestNdvi.toFixed(2)}` : 'N/A'}
                            />
                          </td>
                          <td className="p-4">
                            {cropSpecies}
                          </td>
                          <td className="p-4">
                            {areaHa} ha
                          </td>
                          <td className="p-4 text-right">
                            <button
                              className="text-white bg-green-600 hover:bg-green-700 px-3 py-1.5 rounded-md text-xs font-medium inline-flex items-center gap-1 transition-colors"
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedEntityId(parcel.id);
                              }}
                            >
                              {t('dashboard.analyze')} <ChevronRight className="w-3 h-3" />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {parcels.length === 0 && (
                      <tr>
                        <td colSpan={5} className="p-8 text-center text-slate-400">
                          {t('dashboard.noParcels')}
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
            ← {t('common.back')}
          </button>
          <div className="h-4 w-px bg-gray-300"></div>
          <h2 className="font-semibold text-slate-800">
            {parcels.find((p: any) => p.id === selectedEntityId)?.name?.value ||
              parcels.find((p: any) => p.id === selectedEntityId)?.name ||
              t('dashboard.detailAnalysis')}
          </h2>
        </div>
      </div>

      {/* Content Area — single view (S1: read-only ParcelDetail; legacy
          VegetationAnalytics retained but unused while new flow ramps up). */}
      <div className="flex-1 overflow-auto bg-slate-50">
        <ParcelDetail />
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
