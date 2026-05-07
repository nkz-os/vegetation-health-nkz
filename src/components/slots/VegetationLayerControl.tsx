/**
 * Vegetation Layer Control — context-panel slot (right side of the unified
 * viewer).
 *
 * Read-only consumer per the redesign spec: the user picks an active crop
 * season + an index + opacity here, but does NOT trigger jobs from the
 * viewer. Job creation, deletion, custom formulas, VRA and exports all
 * live in the module's parcel-detail page (linked from this slot).
 */
import React, { useEffect, useMemo } from 'react';
import { Leaf, X, ExternalLink } from 'lucide-react';
import { SlotShell } from '@nekazari/viewer-kit';
import { Stack, Slider, Spinner } from '@nekazari/ui-kit';
import { useTranslation, useViewer } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { IndexPillSelector, type CustomIndexOption } from '../widgets/IndexPillSelector';

const vegetationAccent = { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' };

const VegetationLayerControl: React.FC = () => {
  const { t } = useTranslation();
  const { setCurrentDate } = useViewer();

  const {
    selectedIndex,
    selectedDate,
    selectedEntityId,
    selectedSceneId,
    selectedSeasonId,
    indexResults,
    entityDataStatus,
    entityDataStatusLoading,
    entityName,
    layerOpacity,
    activeJobId,
    activeRasterPath,
    setSelectedIndex,
    setLayerOpacity,
    setIndexResults,
    setSelectedEntityId,
    setSelectedSeasonId,
  } = useVegetationContext();

  const api = useVegetationApi();

  const opacity = layerOpacity;
  const setOpacity = setLayerOpacity;
  const hasLayer = !!(activeJobId || activeRasterPath);

  // Custom index options derived from already-loaded results
  const customIndexOptions: CustomIndexOption[] = useMemo(() => {
    return Object.values(indexResults)
      .filter((r: any) => r.is_custom && r.formula_id)
      .map((r: any) => ({
        key: `custom:${r.formula_id}`,
        label: r.formula_name || r.index_type,
      }));
  }, [indexResults]);

  // Sync the host viewer's currentDate with the slot's selectedDate
  const lastSyncedDateRef = React.useRef<number>(0);
  useEffect(() => {
    if (!selectedDate || !setCurrentDate) return;
    const ts = selectedDate.getTime();
    if (ts === lastSyncedDateRef.current) return;
    lastSyncedDateRef.current = ts;
    setCurrentDate(selectedDate);
  }, [selectedDate, setCurrentDate]);

  // Re-fetch results scoped to the user-selected scene
  useEffect(() => {
    if (!selectedEntityId || !selectedSceneId) return;
    api.getEntityResults(selectedEntityId, { sceneId: selectedSceneId })
      .then((data) => {
        if (data.indices && Object.keys(data.indices).length > 0) {
          setIndexResults(data.indices);
        }
      })
      .catch(() => { /* scene may lack results for some indices */ });
  }, [selectedEntityId, selectedSceneId]);

  const cropSeasons = entityDataStatus?.active_crop_seasons || [];

  // Default the season selection to the first active season once available
  useEffect(() => {
    if (selectedEntityId && !selectedSeasonId && cropSeasons.length > 0) {
      setSelectedSeasonId(cropSeasons[0].id);
    }
  }, [selectedEntityId, selectedSeasonId, cropSeasons, setSelectedSeasonId]);

  const displayName = entityName || (selectedEntityId ? selectedEntityId.split(':').pop() : '');
  // Use ?entityId=... to match the App.tsx URL-param parser; this lets the
  // detail page open already focused on the same parcel the user is viewing.
  const detailHref = selectedEntityId
    ? `/vegetation?entityId=${encodeURIComponent(selectedEntityId)}`
    : '/vegetation';

  // ── No entity selected ─────────────────────────────────────────────────
  if (!selectedEntityId) {
    return (
      <SlotShell moduleId="vegetation-prime" accent={vegetationAccent}>
        <div className="flex items-center justify-center gap-nkz-inline py-nkz-section text-nkz-text-muted">
          <Leaf className="w-5 h-5 opacity-50" />
          <p className="text-nkz-sm">{t('layerControl.selectParcel', 'Pick a parcel to load layers')}</p>
        </div>
      </SlotShell>
    );
  }

  // ── Loading ────────────────────────────────────────────────────────────
  if (entityDataStatusLoading && !entityDataStatus) {
    return (
      <SlotShell moduleId="vegetation-prime" title={t('layerControl.moduleTitle', 'Vegetation')} icon={<Leaf className="w-4 h-4" />} collapsible accent={vegetationAccent}>
        <div className="flex items-center justify-center py-nkz-section">
          <Spinner size="sm" />
        </div>
      </SlotShell>
    );
  }

  const hasAnyData = entityDataStatus?.has_any_data || Object.keys(indexResults).length > 0;

  // ── Read-only consumption panel ────────────────────────────────────────
  return (
    <SlotShell
      moduleId="vegetation-prime"
      title={t('layerControl.moduleTitle', 'Vegetation')}
      icon={<Leaf className="w-4 h-4" />}
      collapsible
      accent={vegetationAccent}
    >
      <Stack gap="stack">
        {/* Header: parcel name + clear */}
        <div className="flex items-center justify-between">
          <span
            className="text-nkz-sm font-medium text-nkz-text-primary truncate"
            title={displayName as string}
          >
            {displayName}
          </span>
          <button
            onClick={() => setSelectedEntityId(null)}
            className="text-nkz-xs text-nkz-text-muted hover:text-nkz-text-primary"
            title={t('layerControl.clearSelection', 'Clear selection')}
            aria-label={t('layerControl.clearSelection', 'Clear selection')}
          >
            <X className="w-3.5 h-3.5" aria-hidden="true" />
          </button>
        </div>

        <p className="text-[11px] text-nkz-text-muted leading-snug">
          {t(
            'layerControl.viewerHint',
            'Browse computed indices on the map. Open the parcel detail page to launch new analyses, edit campaigns or export.',
          )}
        </p>

        {/* Crop season selector — only when more than one is active */}
        {cropSeasons.length > 1 && (
          <div className="space-y-nkz-tight">
            <label className="text-nkz-xs font-semibold uppercase tracking-wider text-nkz-text-muted">
              {t('layerControl.season', 'Season')}
            </label>
            <select
              value={selectedSeasonId || ''}
              onChange={(e) => setSelectedSeasonId(e.target.value || null)}
              className="w-full px-2 py-1.5 text-nkz-sm border border-nkz-border rounded-nkz-md bg-nkz-surface text-nkz-text-primary"
            >
              {cropSeasons.map((s) => (
                <option key={s.id} value={s.id}>
                  {t(`cropSeason.${s.crop_type}`, s.crop_type)}
                  {' '}{s.start_date}{' → '}{s.end_date || '…'}
                </option>
              ))}
            </select>
          </div>
        )}
        {cropSeasons.length === 1 && (
          <div className="text-nkz-xs text-nkz-text-muted">
            {t('layerControl.seasonSingle', 'Season')}:{' '}
            <span className="text-nkz-text-primary font-medium">
              {t(`cropSeason.${cropSeasons[0].crop_type}`, cropSeasons[0].crop_type)}
              {' '}{cropSeasons[0].start_date}{' → '}{cropSeasons[0].end_date || '…'}
            </span>
          </div>
        )}
        {cropSeasons.length === 0 && (
          <div className="text-nkz-xs text-nkz-text-muted italic">
            {t('layerControl.noSeasonHint', 'No active campaign yet. Create one in the parcel detail page.')}
          </div>
        )}

        {/* Index selector with availability gating */}
        <div className="space-y-nkz-tight">
          <label className="text-nkz-xs font-semibold uppercase tracking-wider text-nkz-text-muted">
            {t('layerControl.spectralIndex', 'Spectral index')}
          </label>
          <IndexPillSelector
            selectedIndex={selectedIndex || 'NDVI'}
            onIndexChange={(idx) => setSelectedIndex(idx)}
            customIndexOptions={customIndexOptions}
            availableIndices={entityDataStatus?.available_indices}
          />
          <p className="text-[10px] text-nkz-text-muted">
            {t(
              'layerControl.indexHint',
              'Only indices with computed data are clickable. Run an analysis on the parcel detail page to add more.',
            )}
          </p>
        </div>

        {/* Opacity — only when a layer is loaded */}
        {hasLayer ? (
          <Slider
            value={opacity}
            onChange={setOpacity}
            min={0}
            max={100}
            step={1}
            label={t('layerControl.opacity', 'Opacity')}
            unit="%"
          />
        ) : (
          <div className="pt-nkz-stack border-t border-nkz-border">
            <p className="text-nkz-xs text-nkz-text-muted italic">
              {hasAnyData
                ? t('layerControl.selectIndex', 'Pick an index to load its layer')
                : t('layerControl.noDataYet', 'No data yet — open the detail page to launch an analysis')}
            </p>
          </div>
        )}

        {/* Active scene context */}
        {selectedDate && (
          <div className="text-[11px] text-nkz-text-muted pt-nkz-stack border-t border-nkz-border">
            {t('layerControl.activeScene', 'Active scene')}:{' '}
            <span className="text-nkz-text-primary font-medium">
              {selectedDate.toISOString().split('T')[0]}
            </span>
          </div>
        )}

        {/* Open detail page */}
        <a
          href={detailHref}
          className="inline-flex items-center justify-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-nkz-md bg-nkz-accent-base text-nkz-text-on-accent hover:bg-nkz-accent-strong transition-colors"
        >
          <ExternalLink className="w-3.5 h-3.5" />
          {t('layerControl.openDetail', 'Open parcel detail')}
        </a>
      </Stack>
    </SlotShell>
  );
};

export default VegetationLayerControl;
