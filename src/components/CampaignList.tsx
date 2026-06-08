/**
 * CampaignList — standalone campaign management widget.
 *
 * Displays crop seasons (campaigns) for a parcel with stop/delete actions.
 * Designed for use in slot panels or as an alternative seasons view.
 */

import React, { useState } from 'react';
import { Loader2, Square, Trash2 } from 'lucide-react';
import { useTranslation } from '@nekazari/sdk';

export interface Campaign {
  id: string;
  start_date: string;
  end_date: string | null;
  status: 'active' | 'completed';
  config?: { indices: string[] };
  job_count: number;
}

const fmtDate = (iso: string | null) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
};

export const CampaignList: React.FC<{
  parcelId: string;
  campaigns: Campaign[];
  onStop: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onRefresh: () => void;
}> = ({ campaigns, onStop, onDelete, onRefresh }) => {
  const { t } = useTranslation();
  const [deleting, setDeleting] = useState<string | null>(null);
  const [stopping, setStopping] = useState<string | null>(null);

  if (campaigns.length === 0) {
    return (
      <p className="text-xs text-slate-400 italic py-3 px-4">
        {t('campaignList.empty', 'No active campaigns')}
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {campaigns.map((c) => {
        const busy = deleting === c.id || stopping === c.id;
        return (
          <div
            key={c.id}
            className="flex items-center justify-between p-3 rounded-lg bg-slate-50 border border-slate-200"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={`inline-block text-[11px] px-2 py-0.5 rounded-full font-medium ${
                    c.status === 'active'
                      ? 'bg-emerald-100 text-emerald-700'
                      : 'bg-slate-200 text-slate-500'
                  }`}
                >
                  {c.status === 'active'
                    ? t('campaignList.active', 'Active')
                    : t('campaignList.completed', 'Completed')}
                </span>
                <span className="text-sm text-slate-700 truncate">
                  {fmtDate(c.start_date)}
                  {c.end_date ? ` → ${fmtDate(c.end_date)}` : ` → ${t('campaignList.ongoing', 'ongoing')}`}
                </span>
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-[11px] text-slate-500">
                  {c.job_count} {t('campaignList.jobs', 'jobs')}
                </span>
                {c.config?.indices && c.config.indices.length > 0 && (
                  <span className="text-[11px] text-slate-400">
                    · {c.config.indices.join(', ')}
                  </span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-1.5 shrink-0 ml-3">
              {c.status === 'active' && (
                <button
                  onClick={async () => {
                    setStopping(c.id);
                    try {
                      await onStop(c.id);
                      onRefresh();
                    } finally {
                      setStopping(null);
                    }
                  }}
                  disabled={busy}
                  className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-lg border border-slate-300 bg-white text-slate-600 hover:bg-slate-100 disabled:opacity-50 transition-colors"
                  title={t('campaignList.stopTooltip', 'Stop campaign and cancel pending jobs')}
                >
                  {stopping === c.id ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <Square className="w-3 h-3" />
                  )}
                  {t('campaignList.stop', 'Stop')}
                </button>
              )}
              <button
                onClick={async () => {
                  setDeleting(c.id);
                  try {
                    await onDelete(c.id);
                    onRefresh();
                  } finally {
                    setDeleting(null);
                  }
                }}
                disabled={busy}
                className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-lg border border-rose-200 bg-white text-rose-600 hover:bg-rose-50 disabled:opacity-50 transition-colors"
                title={t('campaignList.deleteTooltip', 'Delete campaign and all associated data')}
              >
                {deleting === c.id ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <Trash2 className="w-3 h-3" />
                )}
                {t('campaignList.delete', 'Delete')}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
};
