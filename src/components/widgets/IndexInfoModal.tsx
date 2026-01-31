import React from 'react';
import { X, Info, Beaker, Target, AlertTriangle, Leaf } from 'lucide-react';
import { IndexInfo, getIndexInfo } from '../../data/indexInfo';

interface IndexInfoModalProps {
    indexType: string;
    isOpen: boolean;
    onClose: () => void;
}

export const IndexInfoModal: React.FC<IndexInfoModalProps> = ({
    indexType,
    isOpen,
    onClose
}) => {
    if (!isOpen) return null;

    const info = getIndexInfo(indexType);

    if (!info) {
        return (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                <div className="bg-white rounded-xl p-6 max-w-md">
                    <p className="text-slate-600">Información no disponible para este índice.</p>
                    <button onClick={onClose} className="mt-4 px-4 py-2 bg-slate-200 rounded-lg">Cerrar</button>
                </div>
            </div>
        );
    }

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
            onClick={(e) => e.target === e.currentTarget && onClose()}
        >
            <div className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-hidden animate-in fade-in zoom-in-95 duration-200">
                {/* Header */}
                <div
                    className="px-6 py-4 border-b border-slate-200 flex items-center justify-between"
                    style={{ backgroundColor: `${info.color}15` }}
                >
                    <div className="flex items-center gap-3">
                        <div
                            className="p-2 rounded-lg"
                            style={{ backgroundColor: `${info.color}25`, color: info.color }}
                        >
                            <Leaf className="w-6 h-6" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-slate-900">{info.name}</h2>
                            <p className="text-sm text-slate-600">{info.fullName}</p>
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        className="p-2 hover:bg-slate-100 rounded-lg transition-colors"
                    >
                        <X className="w-5 h-5 text-slate-500" />
                    </button>
                </div>

                {/* Content */}
                <div className="p-6 overflow-y-auto max-h-[calc(90vh-80px)] space-y-6">
                    {/* Description */}
                    <div>
                        <p className="text-slate-700 leading-relaxed">{info.description}</p>
                    </div>

                    {/* Formula */}
                    <div className="bg-slate-50 rounded-lg p-4">
                        <div className="flex items-center gap-2 mb-2">
                            <Beaker className="w-4 h-4 text-slate-500" />
                            <h3 className="font-semibold text-slate-800">Fórmula</h3>
                        </div>
                        <code className="text-sm font-mono bg-white px-3 py-2 rounded border border-slate-200 block">
                            {info.formula}
                        </code>
                    </div>

                    {/* Interpretation Scale */}
                    <div>
                        <div className="flex items-center gap-2 mb-3">
                            <Info className="w-4 h-4 text-slate-500" />
                            <h3 className="font-semibold text-slate-800">Interpretación de Valores</h3>
                        </div>
                        <div className="space-y-2">
                            {Object.entries(info.interpretation).map(([key, { range, meaning }]) => (
                                <div
                                    key={key}
                                    className="flex items-center gap-3 p-2 rounded-lg bg-slate-50"
                                >
                                    <div
                                        className={`w-3 h-3 rounded-full ${key === 'veryLow' ? 'bg-red-500' :
                                                key === 'low' ? 'bg-orange-400' :
                                                    key === 'medium' ? 'bg-yellow-400' :
                                                        key === 'high' ? 'bg-lime-500' :
                                                            'bg-green-500'
                                            }`}
                                    />
                                    <span className="font-mono text-xs text-slate-600 w-24">{range}</span>
                                    <span className="text-sm text-slate-700">{meaning}</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Best For */}
                    <div>
                        <div className="flex items-center gap-2 mb-3">
                            <Target className="w-4 h-4 text-slate-500" />
                            <h3 className="font-semibold text-slate-800">Mejor Para</h3>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {info.bestFor.map((use, i) => (
                                <span
                                    key={i}
                                    className="px-3 py-1.5 text-sm rounded-full border"
                                    style={{
                                        backgroundColor: `${info.color}10`,
                                        borderColor: `${info.color}30`,
                                        color: info.color
                                    }}
                                >
                                    {use}
                                </span>
                            ))}
                        </div>
                    </div>

                    {/* Limitations */}
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                        <div className="flex items-start gap-2">
                            <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5" />
                            <div>
                                <h3 className="font-semibold text-amber-800 mb-1">Limitaciones</h3>
                                <p className="text-sm text-amber-700">{info.limitations}</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};
