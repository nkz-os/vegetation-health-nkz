/**
 * CropSeasonModal - Single modal for configuring a crop season.
 *
 * Replaces the old 3-step SetupWizard. Simple form with:
 *  - Crop type dropdown
 *  - Start date picker
 *  - Toggle for STAC-based monitoring
 *  - Optional end date picker
 *
 * Calls POST /api/vegetation/crop-seasons/{entity_id} on save.
 */

import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { Button } from '@nekazari/ui-kit';
import { X, Sprout, Calendar, Satellite } from 'lucide-react';
import { useTranslation } from '@nekazari/sdk';
import { useVegetationApi } from '../../services/api';

const CROP_TYPES = [
  'wheat',
  'corn',
  'barley',
  'vineyard',
  'olive',
  'almond',
  'vegetables',
  'other',
] as const;

interface CropSeasonModalProps {
  open: boolean;
  onClose: () => void;
  entityId: string;
  entityName?: string;
  onComplete: () => void;
}

export const SetupWizard: React.FC<CropSeasonModalProps> = ({
  open, onClose, entityId, entityName, onComplete,
}) => {
  const { t } = useTranslation();
  const api = useVegetationApi();

  const [cropType, setCropType] = useState<string>('');
  const [startDate, setStartDate] = useState<string>(() => {
    const d = new Date();
    d.setDate(d.getDate() - 21);
    return d.toISOString().split('T')[0];
  });
  const [endDate, setEndDate] = useState<string>('');
  const [monitoringEnabled, setMonitoringEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  if (!open) return null;

  const handleSave = async () => {
    if (!cropType) {
      setError(t('cropSeason.cropPlaceholder'));
      return;
    }
    setSaving(true);
    setError(null);
    setSuccess(false);

    try {
      await api.createCropSeason(entityId, {
        crop_type: cropType,
        start_date: startDate,
        end_date: endDate || null,
        monitoring_enabled: monitoringEnabled,
      });
      setSuccess(true);
      onComplete();
      setTimeout(onClose, 800);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || t('cropSeason.error');
      setError(typeof msg === 'string' ? msg : t('cropSeason.error'));
    } finally {
      setSaving(false);
    }
  };

  const displayName = entityName || entityId.split(':').pop() || entityId;

  return createPortal(
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-white rounded-lg shadow-xl max-w-lg w-full flex flex-col max-h-[85vh] animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b flex-shrink-0">
          <div className="flex items-center gap-2">
            <Sprout className="w-5 h-5 text-emerald-600" />
            <h2 className="text-lg font-semibold text-slate-800">{t('cropSeason.title')}</h2>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 rounded transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {/* Parcel name */}
          <p className="text-sm text-slate-500">
            <span className="font-medium text-slate-700">{displayName}</span>
          </p>

          {/* Crop type */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">
              <Sprout className="inline w-4 h-4 mr-1" /> {t('cropSeason.cropType')}
            </label>
            <select
              value={cropType}
              onChange={(e) => setCropType(e.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-emerald-500 focus:ring-emerald-500 bg-white"
            >
              <option value="">{t('cropSeason.cropPlaceholder')}</option>
              {CROP_TYPES.map((ct) => (
                <option key={ct} value={ct}>
                  {t(`cropSeason.${ct}`)}
                </option>
              ))}
            </select>
          </div>

          {/* Start date */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">
              <Calendar className="inline w-4 h-4 mr-1" /> {t('cropSeason.startDate')}
            </label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              max={new Date().toISOString().split('T')[0]}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-emerald-500 focus:ring-emerald-500"
            />
          </div>

          {/* Monitoring toggle */}
          <div className="flex items-start gap-3 p-3 bg-emerald-50 rounded-lg border border-emerald-200">
            <input
              type="checkbox"
              id="monitoring-toggle"
              checked={monitoringEnabled}
              onChange={(e) => setMonitoringEnabled(e.target.checked)}
              className="mt-0.5 h-4 w-4 text-emerald-600 focus:ring-emerald-500 border-slate-300 rounded"
            />
            <div>
              <label htmlFor="monitoring-toggle" className="text-sm font-medium text-emerald-800 cursor-pointer">
                <Satellite className="inline w-4 h-4 mr-1" /> {t('cropSeason.monitoringToggle')}
              </label>
              <p className="text-xs text-emerald-600 mt-0.5">{t('cropSeason.monitoringDesc')}</p>
            </div>
          </div>

          {/* End date (optional) */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">
              <Calendar className="inline w-4 h-4 mr-1" /> {t('cropSeason.endDate')}
            </label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              min={startDate || undefined}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-emerald-500 focus:ring-emerald-500"
            />
            <p className="text-xs text-slate-400 mt-1">{t('cropSeason.endDateHelp')}</p>
          </div>

          {/* Error */}
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded text-sm">
              {error}
            </div>
          )}

          {/* Success */}
          {success && (
            <div className="bg-emerald-50 border border-emerald-200 text-emerald-700 p-3 rounded text-sm">
              {t('cropSeason.success')}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-slate-200 p-4 flex-shrink-0 bg-white rounded-b-lg flex justify-end gap-3">
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            onClick={handleSave}
            disabled={saving || !cropType}
            className="bg-emerald-600 hover:bg-emerald-700"
          >
            {saving ? t('cropSeason.saving') : t('cropSeason.save')}
          </Button>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default SetupWizard;
