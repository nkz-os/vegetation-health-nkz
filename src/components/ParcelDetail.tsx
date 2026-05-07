/**
 * ParcelDetail — single-parcel page for the redesigned module UX.
 *
 * Slice 1 scope (read-only):
 *  - Header with parcel name + "go to viewer" link
 *  - Current state (latest completed index)
 *  - Active seasons with their jobs grouped beneath
 *  - Legacy "Sin campaña" bucket for jobs without a season FK
 *  - Recent cloud-skip banner
 *
 * Future slices add: delete buttons (S2), analyze per season (S3),
 * custom formulas + advanced/VRA + export (S4-5).
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Loader2,
  ExternalLink,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Info,
  Trash2,
  Plus,
  Play,
  Sprout,
  Map as MapIcon,
  Download,
  Beaker,
} from 'lucide-react';
import type { CustomFormula } from '../types';
import { useTranslation } from '@nekazari/sdk';
import { useVegetationApi } from '../services/api';
import { useVegetationContext } from '../services/vegetationContext';
import type { ParcelOverview, ParcelSeasonCard, ParcelJobCard } from '../types';

const STANDARD_INDICES = ['NDVI', 'EVI', 'SAVI', 'GNDVI', 'NDRE'] as const;
const COMMON_CROP_TYPES = ['wheat', 'barley', 'corn', 'sunflower', 'vineyard', 'olive', 'other'] as const;

const fmtDate = (iso: string | null) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
};

const fmtNumber = (n: number | null | undefined, digits = 3) =>
  n == null ? '—' : Number(n).toFixed(digits);

const StatusPill: React.FC<{ status: string }> = ({ status }) => {
  const { t } = useTranslation();
  const map: Record<string, { cls: string; label: string }> = {
    completed: { cls: 'bg-emerald-100 text-emerald-700', label: t('parcelDetail.statusCompleted', 'Completed') },
    running: { cls: 'bg-blue-100 text-blue-700', label: t('parcelDetail.statusRunning', 'Running') },
    pending: { cls: 'bg-slate-100 text-slate-600', label: t('parcelDetail.statusPending', 'Pending') },
    failed: { cls: 'bg-rose-100 text-rose-700', label: t('parcelDetail.statusFailed', 'Failed') },
    skipped: { cls: 'bg-amber-100 text-amber-800', label: t('parcelDetail.statusSkipped', 'Skipped (clouds)') },
    cancelled: { cls: 'bg-slate-100 text-slate-500', label: t('parcelDetail.statusCancelled', 'Cancelled') },
  };
  const { cls, label } = map[status] || { cls: 'bg-slate-100 text-slate-600', label: status };
  return <span className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-medium ${cls}`}>{label}</span>;
};

interface JobRowProps {
  job: ParcelJobCard;
  onDelete: (jobId: string) => Promise<void>;
}

const JobRow: React.FC<JobRowProps> = ({ job, onDelete }) => {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const handleDelete = async () => {
    setBusy(true);
    try {
      await onDelete(job.id);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const cascadeWarning =
    job.type === 'download'
      ? t(
          'parcelDetail.deleteCascadeWarning',
          'This will also delete every index calculated from the same scene.',
        )
      : '';

  return (
    <li className="flex items-start gap-3 py-2 px-3 rounded-lg hover:bg-slate-50 border border-transparent hover:border-slate-100">
      <div className="shrink-0 w-1 self-stretch rounded-full bg-slate-200" />
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={job.status} />
          <span className="text-xs font-medium text-slate-700">
            {job.type === 'download' ? t('parcelDetail.jobTypeDownload', 'Scene') : t('parcelDetail.jobTypeIndex', 'Index')}
            {job.indices.length > 0 && (
              <span className="text-slate-500"> · {job.indices.join(', ')}</span>
            )}
            {job.index_type && job.indices.length === 0 && (
              <span className="text-slate-500"> · {job.index_type}</span>
            )}
          </span>
          {job.sensing_date && (
            <span className="text-[11px] text-slate-500">{t('parcelDetail.sensingDate', 'Sensed')}: {fmtDate(job.sensing_date)}</span>
          )}
          {job.local_cloud_pct != null && (
            <span className="text-[11px] text-slate-500">
              {t('parcelDetail.cloudPct', 'Clouds')}: {Math.round(job.local_cloud_pct)}%
            </span>
          )}
          {job.stats_mean != null && (
            <span className="text-[11px] text-slate-500">μ {fmtNumber(job.stats_mean)}</span>
          )}
        </div>
        {job.error_message && (
          <p className="text-[11px] text-rose-600 mt-1 truncate">{job.error_message}</p>
        )}
        {confirming && (
          <div className="mt-2 flex items-center gap-2 bg-rose-50 border border-rose-200 rounded-lg px-2 py-1.5 text-[11px]">
            <span className="text-rose-700">
              {t('parcelDetail.deleteConfirm', 'Delete this job and its raster?')}
              {cascadeWarning && <> {cascadeWarning}</>}
            </span>
            <button
              onClick={handleDelete}
              disabled={busy}
              className="px-2 py-0.5 rounded bg-rose-600 text-white font-semibold disabled:opacity-50"
            >
              {busy ? t('parcelDetail.deleting', 'Deleting…') : t('parcelDetail.deleteYes', 'Delete')}
            </button>
            <button
              onClick={() => setConfirming(false)}
              disabled={busy}
              className="px-2 py-0.5 rounded text-rose-700 hover:bg-rose-100"
            >
              {t('parcelDetail.deleteCancel', 'Cancel')}
            </button>
          </div>
        )}
      </div>
      <div className="shrink-0 flex items-start gap-2">
        <div className="text-[11px] text-slate-400 text-right">
          <div>{fmtDate(job.created_at)}</div>
          {job.created_by && <div className="truncate max-w-[120px]">{job.created_by}</div>}
        </div>
        <button
          onClick={() => setConfirming(true)}
          disabled={confirming || busy}
          className="p-1.5 text-slate-300 hover:text-rose-600 hover:bg-rose-50 rounded transition-colors disabled:opacity-30"
          title={t('parcelDetail.deleteTooltip', 'Delete this job (irreversible)')}
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </li>
  );
};

// ─── Analyze-in-season form ────────────────────────────────────────────────
interface AnalyzeFormProps {
  entityId: string;
  seasonId: string;
  onLaunched: () => void;
}

const AnalyzeInSeasonForm: React.FC<AnalyzeFormProps> = ({ entityId, seasonId, onLaunched }) => {
  const { t } = useTranslation();
  const api = useVegetationApi();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([...STANDARD_INDICES]);
  const [threshold, setThreshold] = useState<number>(30);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
  }, []);
  const [success, setSuccess] = useState<string | null>(null);

  const toggle = (idx: string) =>
    setSelected((prev) => (prev.includes(idx) ? prev.filter((x) => x !== idx) : [...prev, idx]));

  const handleSubmit = async () => {
    if (selected.length === 0) {
      setError(t('parcelDetail.analyzeNoIndex', 'Pick at least one index to compute.'));
      return;
    }
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await api.analyzeInSeason(entityId, seasonId, {
        indices: selected,
        local_cloud_threshold: threshold,
      });
      setSuccess(
        t('parcelDetail.analyzeSuccess', '{{n}} job(s) dispatched, {{s}} scenes found.', {
          n: res.job_ids.length,
          s: res.scenes_found,
        }),
      );
      onLaunched();
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
      closeTimerRef.current = setTimeout(() => setOpen(false), 1500);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full mt-2 inline-flex items-center justify-center gap-2 text-xs font-medium px-3 py-2 rounded-lg border border-dashed border-emerald-300 text-emerald-700 hover:bg-emerald-50 transition-colors"
      >
        <Play className="w-3.5 h-3.5" />
        {t('parcelDetail.analyzeOpen', 'Analyze in this season')}
      </button>
    );
  }

  return (
    <div className="mt-2 bg-white border border-slate-200 rounded-lg p-3 space-y-3">
      <p className="text-[11px] text-slate-500">
        {t(
          'parcelDetail.analyzeHint',
          'Searches Sentinel-2 scenes inside the season window and dispatches one download job per dekadal window. Each download triggers index calculations for the selected indices.',
        )}
      </p>

      {/* Index selector */}
      <div>
        <p className="text-[11px] font-medium text-slate-600 mb-1.5">
          {t('parcelDetail.analyzeIndices', 'Indices to compute')}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {STANDARD_INDICES.map((idx) => {
            const on = selected.includes(idx);
            return (
              <button
                key={idx}
                onClick={() => toggle(idx)}
                disabled={busy}
                className={`px-2.5 py-1 rounded-full text-xs font-semibold border transition-colors ${
                  on
                    ? 'bg-emerald-600 text-white border-emerald-600'
                    : 'bg-white text-slate-600 border-slate-300 hover:border-emerald-400'
                }`}
              >
                {idx}
              </button>
            );
          })}
        </div>
      </div>

      {/* Cloud tolerance slider */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-[11px] font-medium text-slate-600">
            {t('parcelDetail.analyzeCloudTolerance', 'Cloud tolerance over parcel')}
          </label>
          <span className="text-[11px] font-mono text-emerald-700">{threshold}%</span>
        </div>
        <input
          type="range"
          min={5}
          max={80}
          step={5}
          value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          disabled={busy}
          className="w-full h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-emerald-600"
        />
        <p className="text-[10px] text-slate-500 mt-0.5">
          {t(
            'parcelDetail.analyzeCloudHint',
            '10% strict · 30% balanced (default) · 50% permissive (Atlantic climate).',
          )}
        </p>
      </div>

      {error && (
        <p className="text-xs text-rose-600 bg-rose-50 border border-rose-200 rounded p-2">{error}</p>
      )}
      {success && (
        <p className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded p-2">
          {success}
        </p>
      )}

      <div className="flex items-center gap-2 justify-end">
        <button
          onClick={() => setOpen(false)}
          disabled={busy}
          className="text-xs px-3 py-1.5 rounded-lg text-slate-500 hover:bg-slate-100"
        >
          {t('parcelDetail.cancel', 'Cancel')}
        </button>
        <button
          onClick={handleSubmit}
          disabled={busy || selected.length === 0}
          className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-semibold hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
          {busy
            ? t('parcelDetail.analyzeSubmitting', 'Dispatching…')
            : t('parcelDetail.analyzeSubmit', 'Launch analysis')}
        </button>
      </div>
    </div>
  );
};

// ─── New season form ───────────────────────────────────────────────────────
interface NewSeasonFormProps {
  entityId: string;
  onCreated: () => void;
}

const NewSeasonForm: React.FC<NewSeasonFormProps> = ({ entityId, onCreated }) => {
  const { t } = useTranslation();
  const api = useVegetationApi();
  const [open, setOpen] = useState(false);
  const [cropType, setCropType] = useState<string>('wheat');
  const [startDate, setStartDate] = useState<string>('');
  const [endDate, setEndDate] = useState<string>('');
  const [label, setLabel] = useState<string>('');
  const [monitoring, setMonitoring] = useState<boolean>(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setCropType('wheat');
    setStartDate('');
    setEndDate('');
    setLabel('');
    setMonitoring(false);
    setError(null);
  };

  const handleSubmit = async () => {
    if (!startDate) {
      setError(t('parcelDetail.seasonStartRequired', 'Start date is required.'));
      return;
    }
    if (endDate && endDate < startDate) {
      setError(t('parcelDetail.seasonDateOrder', 'End date must be on or after start date.'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createCropSeason(entityId, {
        crop_type: cropType,
        start_date: startDate,
        end_date: endDate || null,
        label: label.trim() || null,
        monitoring_enabled: monitoring,
      });
      reset();
      setOpen(false);
      onCreated();
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err?.message || String(err);
      setError(
        status === 409
          ? t(
              'parcelDetail.seasonConflict',
              'This season overlaps an existing one. Adjust the dates or remove the conflicting season.',
            ) + ` (${detail})`
          : detail,
      );
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full inline-flex items-center justify-center gap-2 text-xs font-medium px-3 py-2.5 rounded-xl border border-dashed border-emerald-300 text-emerald-700 hover:bg-emerald-50 transition-colors"
      >
        <Plus className="w-4 h-4" />
        {t('parcelDetail.newSeason', 'New crop season')}
      </button>
    );
  }

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Sprout className="w-4 h-4 text-emerald-600" />
        <h3 className="text-sm font-semibold text-slate-700">
          {t('parcelDetail.newSeasonTitle', 'New crop season')}
        </h3>
      </div>
      <p className="text-[11px] text-slate-500">
        {t(
          'parcelDetail.newSeasonHint',
          'Pick a crop and a date range. Two seasons cannot overlap on the same parcel — pick non-conflicting dates.',
        )}
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div>
          <label className="block text-[11px] font-medium text-slate-600 mb-0.5">
            {t('parcelDetail.cropTypeLabel', 'Crop')}
          </label>
          <select
            value={cropType}
            onChange={(e) => setCropType(e.target.value)}
            disabled={busy}
            className="w-full px-2.5 py-1.5 border border-slate-300 rounded-lg bg-white"
          >
            {COMMON_CROP_TYPES.map((c) => (
              <option key={c} value={c}>
                {t(`cropSeason.${c}`, c)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-[11px] font-medium text-slate-600 mb-0.5">
            {t('parcelDetail.labelLabel', 'Label (optional)')}
          </label>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={t('parcelDetail.labelPlaceholder', 'e.g. Wheat 2025') as string}
            disabled={busy}
            className="w-full px-2.5 py-1.5 border border-slate-300 rounded-lg"
          />
        </div>
        <div>
          <label className="block text-[11px] font-medium text-slate-600 mb-0.5">
            {t('parcelDetail.startDateLabel', 'Start date')}
          </label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            disabled={busy}
            className="w-full px-2.5 py-1.5 border border-slate-300 rounded-lg"
          />
        </div>
        <div>
          <label className="block text-[11px] font-medium text-slate-600 mb-0.5">
            {t('parcelDetail.endDateLabel', 'End date (optional)')}
          </label>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            disabled={busy}
            className="w-full px-2.5 py-1.5 border border-slate-300 rounded-lg"
          />
        </div>
      </div>

      <label className="inline-flex items-center gap-2 text-xs text-slate-600">
        <input
          type="checkbox"
          checked={monitoring}
          onChange={(e) => setMonitoring(e.target.checked)}
          disabled={busy}
          className="accent-emerald-600"
        />
        {t('parcelDetail.monitoringLabel', 'Enable continuous monitoring (auto-process new scenes)')}
      </label>

      {error && (
        <p className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">{error}</p>
      )}

      <div className="flex items-center gap-2 justify-end">
        <button
          onClick={() => {
            reset();
            setOpen(false);
          }}
          disabled={busy}
          className="text-xs px-3 py-1.5 rounded-lg text-slate-500 hover:bg-slate-100"
        >
          {t('parcelDetail.cancel', 'Cancel')}
        </button>
        <button
          onClick={handleSubmit}
          disabled={busy}
          className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-semibold hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
          {busy ? t('parcelDetail.creating', 'Creating…') : t('parcelDetail.createSeason', 'Create season')}
        </button>
      </div>
    </div>
  );
};

// ─── Advanced section ─────────────────────────────────────────────────────
interface AdvancedSectionProps {
  entityId: string;
  defaultIndex: string | null;
  onAction: () => void;
}

const AdvancedSection: React.FC<AdvancedSectionProps> = ({ entityId, defaultIndex, onAction }) => {
  const { t } = useTranslation();
  const api = useVegetationApi();
  const [open, setOpen] = useState(false);

  // ─── VRA Zoning ────────────────────────────────────────────────────────
  const [vraSource, setVraSource] = useState<string>(defaultIndex || 'NDVI');
  const [vraBusy, setVraBusy] = useState(false);
  const [vraMsg, setVraMsg] = useState<{ type: 'ok' | 'error'; text: string } | null>(null);

  const handleVra = async () => {
    setVraBusy(true);
    setVraMsg(null);
    try {
      const res = await api.calculateIndex({
        entity_id: entityId,
        index_type: 'VRA_ZONES' as any,
      });
      setVraMsg({
        type: 'ok',
        text: t('parcelDetail.vraDispatched', 'Zoning job dispatched (id {{id}})', {
          id: res.job_id.slice(0, 8),
        }),
      });
      onAction();
    } catch (err: any) {
      setVraMsg({
        type: 'error',
        text: err?.response?.data?.detail || err?.message || String(err),
      });
    } finally {
      setVraBusy(false);
    }
  };

  // ─── Custom formulas ───────────────────────────────────────────────────
  const [formulas, setFormulas] = useState<CustomFormula[]>([]);
  const [loadingFormulas, setLoadingFormulas] = useState(false);
  const [formulaName, setFormulaName] = useState('');
  const [formulaExpr, setFormulaExpr] = useState('');
  const [formulaBusy, setFormulaBusy] = useState(false);
  const [formulaMsg, setFormulaMsg] = useState<{ type: 'ok' | 'error'; text: string } | null>(null);

  const loadFormulas = useCallback(async () => {
    setLoadingFormulas(true);
    try {
      const r = await api.listCustomFormulas();
      setFormulas(r.items);
    } catch {
      // Silent: tenant may not have any
    } finally {
      setLoadingFormulas(false);
    }
  }, [api]);

  useEffect(() => {
    if (open) loadFormulas();
  }, [open, loadFormulas]);

  const handleCreateFormula = async () => {
    if (!formulaName.trim() || !formulaExpr.trim()) {
      setFormulaMsg({ type: 'error', text: t('parcelDetail.formulaFieldsRequired', 'Name and expression are required.') });
      return;
    }
    setFormulaBusy(true);
    setFormulaMsg(null);
    try {
      await api.createCustomFormula({ name: formulaName.trim(), formula: formulaExpr.trim() });
      setFormulaName('');
      setFormulaExpr('');
      setFormulaMsg({ type: 'ok', text: t('parcelDetail.formulaCreated', 'Custom formula created.') });
      await loadFormulas();
    } catch (err: any) {
      setFormulaMsg({
        type: 'error',
        text: err?.response?.data?.detail || err?.message || String(err),
      });
    } finally {
      setFormulaBusy(false);
    }
  };

  const handleDeleteFormula = async (id: string) => {
    try {
      await api.deleteCustomFormula(id);
      await loadFormulas();
    } catch (err: any) {
      setFormulaMsg({
        type: 'error',
        text: err?.response?.data?.detail || err?.message || String(err),
      });
    }
  };

  // ─── Export ────────────────────────────────────────────────────────────
  const [exportBusy, setExportBusy] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const downloadBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleExport = async (format: 'geojson' | 'shapefile' | 'csv') => {
    setExportBusy(format);
    setExportError(null);
    try {
      const short = entityId.split(':').pop() || 'parcel';
      const ext = format === 'shapefile' ? 'zip' : format;
      let blob: Blob;
      if (format === 'geojson') blob = await api.exportPrescriptionGeojson(entityId);
      else if (format === 'shapefile') blob = await api.exportPrescriptionShapefile(entityId);
      else blob = await api.exportPrescriptionCsv(entityId);
      downloadBlob(blob, `prescription_${short}.${ext}`);
    } catch (err: any) {
      setExportError(
        err?.response?.data?.detail || err?.message ||
          t('parcelDetail.exportError', 'Export failed (a VRA result is required first).'),
      );
    } finally {
      setExportBusy(null);
    }
  };

  return (
    <div className="border border-slate-200 rounded-xl overflow-hidden bg-white">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-50 transition-colors text-left"
      >
        {open ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-slate-700 text-sm">
            {t('parcelDetail.advancedTitle', 'Advanced')}
          </h3>
          <p className="text-[11px] text-slate-500">
            {t('parcelDetail.advancedHint', 'VRA zoning, custom formulas and exports — for power users.')}
          </p>
        </div>
      </button>

      {open && (
        <div className="border-t border-slate-100 p-4 space-y-5 bg-slate-50/40">
          {/* VRA */}
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5 flex items-center gap-1.5">
              <MapIcon className="w-3.5 h-3.5" />
              {t('parcelDetail.vraTitle', 'VRA zoning')}
            </h4>
            <p className="text-[11px] text-slate-500 mb-2">
              {t(
                'parcelDetail.vraHint',
                'Splits the parcel into prescription zones based on the chosen index. Run this once you have a recent computed index for the parcel.',
              )}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-xs text-slate-600">{t('parcelDetail.vraSourceLabel', 'Source index')}:</label>
              <select
                value={vraSource}
                onChange={(e) => setVraSource(e.target.value)}
                disabled={vraBusy}
                className="text-xs px-2 py-1 border border-slate-300 rounded-lg bg-white"
              >
                {STANDARD_INDICES.map((idx) => (
                  <option key={idx} value={idx}>{idx}</option>
                ))}
              </select>
              <button
                onClick={handleVra}
                disabled={vraBusy}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-semibold hover:bg-emerald-700 disabled:opacity-50"
              >
                {vraBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Beaker className="w-3.5 h-3.5" />}
                {t('parcelDetail.vraGenerate', 'Generate zones')}
              </button>
            </div>
            {vraMsg && (
              <p
                className={`text-xs mt-2 px-2 py-1 rounded ${
                  vraMsg.type === 'ok'
                    ? 'text-emerald-700 bg-emerald-50 border border-emerald-200'
                    : 'text-rose-700 bg-rose-50 border border-rose-200'
                }`}
              >
                {vraMsg.text}
              </p>
            )}
          </div>

          {/* Custom formulas */}
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5 flex items-center gap-1.5">
              <Beaker className="w-3.5 h-3.5" />
              {t('parcelDetail.customFormulasTitle', 'Custom formulas')}
            </h4>
            <p className="text-[11px] text-slate-500 mb-2">
              {t(
                'parcelDetail.customFormulasHint',
                'Define your own band-based formula (e.g. (B08-B11)/(B08+B11) for NDMI). Custom formulas are tenant-wide and can be opted into per analysis.',
              )}
            </p>
            {loadingFormulas ? (
              <p className="text-xs text-slate-400 italic">{t('parcelDetail.loadingFormulas', 'Loading…')}</p>
            ) : formulas.length === 0 ? (
              <p className="text-xs text-slate-400 italic mb-2">
                {t('parcelDetail.formulasEmpty', 'No custom formulas yet.')}
              </p>
            ) : (
              <ul className="space-y-1 mb-2">
                {formulas.map((f) => (
                  <li
                    key={f.id}
                    className="flex items-center gap-2 px-2 py-1.5 bg-white border border-slate-200 rounded-lg text-xs"
                  >
                    <span className="font-medium text-slate-700">{f.name}</span>
                    <span className="font-mono text-[11px] text-slate-500 truncate flex-1">{f.formula}</span>
                    {!f.is_validated && (
                      <span className="text-[10px] text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded">
                        {t('parcelDetail.formulaInvalid', 'invalid')}
                      </span>
                    )}
                    <button
                      onClick={() => handleDeleteFormula(f.id)}
                      className="p-1 text-slate-400 hover:text-rose-600 rounded"
                      title={t('parcelDetail.deleteFormula', 'Delete formula')}
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-1">
              <input
                type="text"
                value={formulaName}
                onChange={(e) => setFormulaName(e.target.value)}
                placeholder={t('parcelDetail.formulaNamePlaceholder', 'Name (e.g. NDMI)') as string}
                disabled={formulaBusy}
                className="text-xs px-2 py-1.5 border border-slate-300 rounded-lg"
              />
              <input
                type="text"
                value={formulaExpr}
                onChange={(e) => setFormulaExpr(e.target.value)}
                placeholder={t('parcelDetail.formulaExprPlaceholder', '(B08-B11)/(B08+B11)') as string}
                disabled={formulaBusy}
                className="text-xs px-2 py-1.5 border border-slate-300 rounded-lg sm:col-span-1 font-mono"
              />
              <button
                onClick={handleCreateFormula}
                disabled={formulaBusy}
                className="inline-flex items-center justify-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-700 text-white font-semibold hover:bg-slate-800 disabled:opacity-50"
              >
                {formulaBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                {t('parcelDetail.addFormula', 'Add formula')}
              </button>
            </div>
            {formulaMsg && (
              <p
                className={`text-xs mt-2 px-2 py-1 rounded ${
                  formulaMsg.type === 'ok'
                    ? 'text-emerald-700 bg-emerald-50 border border-emerald-200'
                    : 'text-rose-700 bg-rose-50 border border-rose-200'
                }`}
              >
                {formulaMsg.text}
              </p>
            )}
          </div>

          {/* Export */}
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5 flex items-center gap-1.5">
              <Download className="w-3.5 h-3.5" />
              {t('parcelDetail.exportTitle', 'Export VRA prescription')}
            </h4>
            <p className="text-[11px] text-slate-500 mb-2">
              {t(
                'parcelDetail.exportHint',
                'Downloads the most recent VRA zoning result. Generate a VRA above first if you have not already.',
              )}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {(['geojson', 'shapefile', 'csv'] as const).map((fmt) => (
                <button
                  key={fmt}
                  onClick={() => handleExport(fmt)}
                  disabled={exportBusy !== null}
                  className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-slate-300 bg-white text-slate-700 hover:bg-slate-100 disabled:opacity-50"
                >
                  {exportBusy === fmt ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                  {fmt.toUpperCase()}
                </button>
              ))}
            </div>
            {exportError && (
              <p className="text-xs mt-2 px-2 py-1 rounded text-rose-700 bg-rose-50 border border-rose-200">
                {exportError}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

interface SeasonBlockProps {
  season: ParcelSeasonCard;
  entityId: string;
  onDelete: (jobId: string) => Promise<void>;
  onLaunched: () => void;
}

const SeasonBlock: React.FC<SeasonBlockProps> = ({ season, entityId, onDelete, onLaunched }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  return (
    <div className="border border-slate-200 rounded-xl overflow-hidden bg-white">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-50 transition-colors text-left"
      >
        {open ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-slate-800 truncate">
            {season.label || `${t(`cropSeason.${season.crop_type}`, season.crop_type)} ${season.start_date?.slice(0, 4) || ''}`}
          </h3>
          <p className="text-xs text-slate-500">
            {fmtDate(season.start_date)} → {season.end_date ? fmtDate(season.end_date) : t('parcelDetail.ongoing', 'ongoing')}
            {' · '}
            {t('parcelDetail.statsLine', '{{total}} jobs · {{ok}} ok · {{skipped}} skipped · {{failed}} failed', {
              total: season.stats.jobs_total,
              ok: season.stats.jobs_completed,
              skipped: season.stats.jobs_skipped,
              failed: season.stats.jobs_failed,
            })}
          </p>
        </div>
        {season.is_active && (
          <span className="shrink-0 inline-flex items-center gap-1 text-[11px] text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            {t('parcelDetail.seasonActive', 'active')}
          </span>
        )}
      </button>
      {open && (
        <div className="border-t border-slate-100 px-3 py-2 bg-slate-50/40">
          {season.jobs.length === 0 ? (
            <p className="text-xs text-slate-400 italic px-3 py-2">
              {t('parcelDetail.seasonEmpty', 'No analyses in this season yet.')}
            </p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {season.jobs.map((j) => (
                <JobRow key={j.id} job={j} onDelete={onDelete} />
              ))}
            </ul>
          )}
          {season.is_active && (
            <div className="px-3 pt-1 pb-3">
              <AnalyzeInSeasonForm
                entityId={entityId}
                seasonId={season.id}
                onLaunched={onLaunched}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const SectionHeader: React.FC<{ title: string; hint: string }> = ({ title, hint }) => (
  <div className="flex items-center gap-2 mb-2">
    <h2 className="text-sm font-bold text-slate-700 uppercase tracking-wider">{title}</h2>
    <span title={hint}>
      <Info className="w-3.5 h-3.5 text-slate-300" aria-hidden="true" />
    </span>
    <p className="text-xs text-slate-500 ml-1">{hint}</p>
  </div>
);

export const ParcelDetail: React.FC = () => {
  const { t } = useTranslation();
  const api = useVegetationApi();
  const { selectedEntityId } = useVegetationContext();
  const [overview, setOverview] = useState<ParcelOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [quota, setQuota] = useState<{ used: number; limit: number; plan: string } | null>(null);
  const [flash, setFlash] = useState<{ kind: 'error' | 'info'; text: string } | null>(null);

  // Inline toast — replaces window.alert() so we never block the Cesium
  // render thread. Self-dismisses after 6s; manual close button below.
  const flashTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const showFlash = useCallback((kind: 'error' | 'info', text: string) => {
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    setFlash({ kind, text });
    flashTimerRef.current = setTimeout(() => setFlash(null), 6000);
  }, []);
  useEffect(() => () => {
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
  }, []);

  const refetch = useCallback(() => setReloadTick((t) => t + 1), []);

  const handleDeleteJob = useCallback(
    async (jobId: string) => {
      if (!selectedEntityId) return;
      try {
        await api.deleteParcelJob(selectedEntityId, jobId);
        refetch();
      } catch (err: any) {
        showFlash(
          'error',
          t('parcelDetail.deleteFailed', 'Could not delete: {{msg}}', {
            msg: err?.message || String(err),
          }),
        );
      }
    },
    [api, selectedEntityId, refetch, t, showFlash],
  );

  useEffect(() => {
    if (!selectedEntityId) {
      setOverview(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getParcelOverview(selectedEntityId)
      .then((data: ParcelOverview) => {
        if (cancelled) return;
        setOverview(data);
      })
      .catch((err: any) => {
        if (cancelled) return;
        setError(err?.message || String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    // Daily quota — non-blocking; 'usage/current' can fail on misconfigured
    // tenants without breaking the page.
    api
      .getCurrentUsage()
      .then((u) => {
        if (cancelled) return;
        setQuota({
          used: u.frequency.used_jobs_today,
          limit: u.frequency.limit_jobs_today,
          plan: u.plan,
        });
      })
      .catch(() => {
        /* ignore */
      });
    return () => {
      cancelled = true;
    };
  }, [selectedEntityId, api, reloadTick]);

  if (!selectedEntityId) {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <div className="flex items-center justify-center h-32 bg-slate-50 rounded-xl border border-dashed border-slate-300">
          <div className="text-center">
            <p className="text-slate-500 font-medium">{t('parcelDetail.noParcelSelected', 'No parcel selected')}</p>
            <p className="text-xs text-slate-400 mt-1">{t('parcelDetail.noParcelHint', 'Pick a parcel from the unified viewer or the parcel list.')}</p>
          </div>
        </div>
      </div>
    );
  }

  if (loading && !overview) {
    return (
      <div className="p-8 flex items-center justify-center text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        {t('parcelDetail.loading', 'Loading parcel detail…')}
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 text-sm text-rose-800">
          <div className="flex items-center gap-2 font-semibold">
            <AlertTriangle className="w-4 h-4" />
            {t('parcelDetail.errorTitle', 'Could not load parcel')}
          </div>
          <p className="mt-1">{error}</p>
        </div>
      </div>
    );
  }

  if (!overview) return null;

  const parcelLabel =
    overview.parcel.name || overview.parcel.entity_id.split(':').pop() || overview.parcel.entity_id;
  const viewerHref = `/?selected=${encodeURIComponent(overview.parcel.entity_id)}`;
  const lastSkip = overview.recent_skips[0];
  const hasAnyData = overview.current_state || overview.seasons.some((s) => s.stats.jobs_completed > 0);

  return (
    <div className="space-y-5 max-w-4xl mx-auto py-6 px-4">
      {/* Inline toast — non-blocking replacement for window.alert */}
      {flash && (
        <div
          role="status"
          aria-live="polite"
          className={`flex items-start gap-2 rounded-xl border px-3 py-2 text-sm ${
            flash.kind === 'error'
              ? 'bg-rose-50 border-rose-200 text-rose-800'
              : 'bg-blue-50 border-blue-200 text-blue-800'
          }`}
        >
          <span className="flex-1">{flash.text}</span>
          <button
            type="button"
            onClick={() => setFlash(null)}
            aria-label={t('parcelDetail.dismiss', 'Dismiss')}
            className="opacity-60 hover:opacity-100"
          >
            ×
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-slate-800 truncate">{parcelLabel}</h1>
          <p className="text-xs text-slate-500 break-all">{overview.parcel.entity_id}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {quota && quota.limit > 0 && (
            <span
              className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border ${
                quota.used >= quota.limit
                  ? 'text-rose-700 bg-rose-50 border-rose-200'
                  : quota.used >= quota.limit * 0.8
                    ? 'text-amber-700 bg-amber-50 border-amber-200'
                    : 'text-slate-600 bg-slate-50 border-slate-200'
              }`}
              title={t('parcelDetail.quotaTooltip', 'Plan: {{plan}}', { plan: quota.plan })}
            >
              {t('parcelDetail.quotaLabel', '{{used}}/{{limit}} jobs today', {
                used: quota.used,
                limit: quota.limit,
              })}
            </span>
          )}
          {overview.active_jobs_count > 0 && (
            <span className="inline-flex items-center gap-1 text-[11px] text-blue-700 bg-blue-50 px-2 py-0.5 rounded-full">
              <Loader2 className="w-3 h-3 animate-spin" />
              {t('parcelDetail.activeJobs', '{{n}} job(s) in progress', { n: overview.active_jobs_count })}
            </span>
          )}
          <a
            href={viewerHref}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            {t('parcelDetail.goToViewer', 'Open in viewer')}
          </a>
        </div>
      </div>

      {/* Cloud-skip banner */}
      {lastSkip && !hasAnyData && (
        <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl p-3 text-sm">
          <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="font-semibold text-amber-900">
              {t('parcelDetail.cloudSkipTitle', 'Last scene was discarded due to clouds')}
            </p>
            <p className="text-amber-800 mt-0.5">
              {lastSkip.local_cloud_pct != null && lastSkip.local_cloud_threshold != null
                ? t('parcelDetail.cloudSkipDetail', 'Clouds over the parcel were {{pct}}% (threshold {{thr}}%). Raise the tolerance or wait for a clearer scene.', {
                    pct: Math.round(lastSkip.local_cloud_pct),
                    thr: Math.round(lastSkip.local_cloud_threshold),
                  })
                : lastSkip.message}
            </p>
          </div>
        </div>
      )}

      {/* Current state */}
      <section>
        <SectionHeader
          title={t('parcelDetail.currentStateTitle', 'Current state')}
          hint={t('parcelDetail.currentStateHint', 'Latest computed index across all analyses for this parcel.')}
        />
        {overview.current_state ? (
          <div className="bg-white border border-slate-200 rounded-xl p-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div>
              <p className="text-[11px] uppercase text-slate-400 tracking-wider">{t('parcelDetail.currentIndex', 'Index')}</p>
              <p className="text-base font-semibold text-slate-800">{overview.current_state.index_type}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase text-slate-400 tracking-wider">{t('parcelDetail.currentValue', 'Mean value')}</p>
              <p className="text-base font-semibold text-slate-800">{fmtNumber(overview.current_state.stats_mean)}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase text-slate-400 tracking-wider">{t('parcelDetail.currentDate', 'Sensed on')}</p>
              <p className="text-base font-semibold text-slate-800">{fmtDate(overview.current_state.sensing_date)}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase text-slate-400 tracking-wider">{t('parcelDetail.currentAvailable', 'Indices available')}</p>
              <p className="text-base font-semibold text-slate-800">{overview.available_indices.length || '—'}</p>
            </div>
          </div>
        ) : (
          <div className="bg-slate-50 border border-dashed border-slate-300 rounded-xl p-4 text-center text-sm text-slate-500">
            {t('parcelDetail.currentStateEmpty', 'No completed analysis yet for this parcel.')}
          </div>
        )}
      </section>

      {/* Seasons */}
      <section>
        <SectionHeader
          title={t('parcelDetail.seasonsTitle', 'Crop seasons')}
          hint={t('parcelDetail.seasonsHint', 'Every analysis belongs to a crop season. Seasons cannot overlap on the same parcel.')}
        />
        {overview.seasons.length === 0 ? (
          <div className="space-y-3">
            <div className="bg-slate-50 border border-dashed border-slate-300 rounded-xl p-4 text-center text-sm text-slate-500">
              {t('parcelDetail.seasonsEmpty', 'No active crop season. Create one to start analysing this parcel.')}
            </div>
            <NewSeasonForm entityId={overview.parcel.entity_id} onCreated={refetch} />
          </div>
        ) : (
          <div className="space-y-2">
            {overview.seasons.map((s) => (
              <SeasonBlock
                key={s.id}
                season={s}
                entityId={overview.parcel.entity_id}
                onDelete={handleDeleteJob}
                onLaunched={refetch}
              />
            ))}
            <NewSeasonForm entityId={overview.parcel.entity_id} onCreated={refetch} />
          </div>
        )}
      </section>

      {/* Legacy bucket */}
      {overview.legacy_jobs.length > 0 && (
        <section>
          <SectionHeader
            title={t('parcelDetail.legacyTitle', 'Without a season (legacy)')}
            hint={t('parcelDetail.legacyHint', 'Older analyses created before crop seasons were mandatory. Safe to delete.')}
          />
          <div className="border border-slate-200 rounded-xl bg-white">
            <ul className="divide-y divide-slate-100">
              {overview.legacy_jobs.map((j) => (
                <JobRow key={j.id} job={j} onDelete={handleDeleteJob} />
              ))}
            </ul>
          </div>
        </section>
      )}

      {/* Advanced (collapsed by default) — VRA, custom formulas, export */}
      <section>
        <AdvancedSection
          entityId={overview.parcel.entity_id}
          defaultIndex={overview.current_state?.index_type || null}
          onAction={refetch}
        />
      </section>
    </div>
  );
};
