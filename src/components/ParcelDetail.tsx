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

import React, { useEffect, useState } from 'react';
import { Loader2, ExternalLink, AlertTriangle, ChevronDown, ChevronRight, Info } from 'lucide-react';
import { useTranslation } from '@nekazari/sdk';
import { useVegetationApi } from '../services/api';
import { useVegetationContext } from '../services/vegetationContext';
import type { ParcelOverview, ParcelSeasonCard, ParcelJobCard } from '../types';

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

const JobRow: React.FC<{ job: ParcelJobCard }> = ({ job }) => {
  const { t } = useTranslation();
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
      </div>
      <div className="shrink-0 text-[11px] text-slate-400 text-right">
        <div>{fmtDate(job.created_at)}</div>
        {job.created_by && <div className="truncate max-w-[120px]">{job.created_by}</div>}
      </div>
    </li>
  );
};

const SeasonBlock: React.FC<{ season: ParcelSeasonCard }> = ({ season }) => {
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
                <JobRow key={j.id} job={j} />
              ))}
            </ul>
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
    return () => {
      cancelled = true;
    };
  }, [selectedEntityId, api]);

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
      {/* Header */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-slate-800 truncate">{parcelLabel}</h1>
          <p className="text-xs text-slate-500 break-all">{overview.parcel.entity_id}</p>
        </div>
        <div className="flex items-center gap-2">
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
          <div className="bg-slate-50 border border-dashed border-slate-300 rounded-xl p-4 text-center text-sm text-slate-500">
            {t('parcelDetail.seasonsEmpty', 'No active crop season. Create one to start analysing this parcel.')}
          </div>
        ) : (
          <div className="space-y-2">
            {overview.seasons.map((s) => (
              <SeasonBlock key={s.id} season={s} />
            ))}
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
                <JobRow key={j.id} job={j} />
              ))}
            </ul>
          </div>
        </section>
      )}
    </div>
  );
};
