/**
 * Information about vegetation indices for educational display
 */

export interface IndexInfo {
    id: string;
    name: string;
    fullName: string;
    description: string;
    formula: string;
    range: [number, number];
    interpretation: {
        veryLow: { range: string; meaning: string };
        low: { range: string; meaning: string };
        medium: { range: string; meaning: string };
        high: { range: string; meaning: string };
        veryHigh: { range: string; meaning: string };
    };
    bestFor: string[];
    limitations: string;
    color: string;
}

export const INDEX_INFO: Record<string, IndexInfo> = {
    NDVI: {
        id: 'NDVI',
        name: 'NDVI',
        fullName: 'Índice de Vegetación de Diferencia Normalizada',
        description: 'El índice más utilizado para medir el verdor y vigor de la vegetación. Detecta la clorofila activa en las plantas.',
        formula: '(NIR - RED) / (NIR + RED)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< 0', meaning: 'Agua, nubes o nieve' },
            low: { range: '0 - 0.2', meaning: 'Suelo desnudo, roca o zonas urbanizadas' },
            medium: { range: '0.2 - 0.4', meaning: 'Vegetación escasa o estresada' },
            high: { range: '0.4 - 0.6', meaning: 'Vegetación moderada, cultivos en crecimiento' },
            veryHigh: { range: '> 0.6', meaning: 'Vegetación densa y saludable' }
        },
        bestFor: ['Cultivos extensivos (cereales, maíz)', 'Monitoreo general de salud', 'Pastos y praderas', 'Detección de estrés'],
        limitations: 'Puede saturarse en vegetación muy densa (bosques). Sensible a efectos atmosféricos.',
        color: '#22c55e'
    },
    EVI: {
        id: 'EVI',
        name: 'EVI',
        fullName: 'Índice de Vegetación Mejorado',
        description: 'Versión mejorada del NDVI que corrige distorsiones atmosféricas y reduce la saturación en zonas de alta biomasa.',
        formula: '2.5 × (NIR - RED) / (NIR + 6×RED - 7.5×BLUE + 1)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< 0', meaning: 'Sin vegetación' },
            low: { range: '0 - 0.2', meaning: 'Vegetación muy escasa' },
            medium: { range: '0.2 - 0.4', meaning: 'Vegetación moderada' },
            high: { range: '0.4 - 0.6', meaning: 'Vegetación densa' },
            veryHigh: { range: '> 0.6', meaning: 'Vegetación muy densa (bosques)' }
        },
        bestFor: ['Bosques densos', 'Zonas tropicales', 'Alta biomasa', 'Estudios forestales'],
        limitations: 'Requiere banda azul (B02). Más complejo de calcular.',
        color: '#16a34a'
    },
    NDWI: {
        id: 'NDWI',
        name: 'NDWI',
        fullName: 'Índice de Agua de Diferencia Normalizada',
        description: 'Detecta el contenido de agua en la vegetación y superficies. Útil para monitoreo de riego y estrés hídrico.',
        formula: '(GREEN - NIR) / (GREEN + NIR)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< -0.3', meaning: 'Vegetación con déficit hídrico severo' },
            low: { range: '-0.3 - 0', meaning: 'Vegetación con estrés hídrico' },
            medium: { range: '0 - 0.2', meaning: 'Contenido de agua normal' },
            high: { range: '0.2 - 0.4', meaning: 'Alto contenido de agua' },
            veryHigh: { range: '> 0.4', meaning: 'Cuerpos de agua o zonas inundadas' }
        },
        bestFor: ['Detección de estrés hídrico', 'Gestión de riego', 'Monitoreo de sequía', 'Detección de inundaciones'],
        limitations: 'Puede confundir sombras con agua. Menos sensible en vegetación escasa.',
        color: '#3b82f6'
    },
    SAVI: {
        id: 'SAVI',
        name: 'SAVI',
        fullName: 'Índice de Vegetación Ajustado al Suelo',
        description: 'Minimiza la influencia del brillo del suelo en zonas con baja cobertura vegetal. Ideal para cultivos jóvenes.',
        formula: '((NIR - RED) / (NIR + RED + L)) × (1 + L)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< 0', meaning: 'Suelo sin vegetación' },
            low: { range: '0 - 0.2', meaning: 'Vegetación emergente o muy escasa' },
            medium: { range: '0.2 - 0.4', meaning: 'Cobertura vegetal parcial' },
            high: { range: '0.4 - 0.6', meaning: 'Buena cobertura vegetal' },
            veryHigh: { range: '> 0.6', meaning: 'Cobertura vegetal completa' }
        },
        bestFor: ['Cultivos jóvenes (primeras semanas)', 'Zonas áridas o semiáridas', 'Viñedos', 'Olivares'],
        limitations: 'Factor L fijo (0.5) puede no ser óptimo para todas las situaciones.',
        color: '#eab308'
    },
    MSAVI: {
        id: 'MSAVI',
        name: 'MSAVI',
        fullName: 'SAVI Modificado',
        description: 'Versión mejorada del SAVI que auto-ajusta el factor de corrección según la densidad de vegetación.',
        formula: '(2×NIR + 1 - √((2×NIR + 1)² - 8×(NIR - RED))) / 2',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< 0', meaning: 'Sin vegetación significativa' },
            low: { range: '0 - 0.2', meaning: 'Vegetación muy escasa' },
            medium: { range: '0.2 - 0.4', meaning: 'Vegetación en desarrollo' },
            high: { range: '0.4 - 0.6', meaning: 'Vegetación bien establecida' },
            veryHigh: { range: '> 0.6', meaning: 'Vegetación densa' }
        },
        bestFor: ['Agricultura de precisión', 'Zonas con vegetación heterogénea', 'Seguimiento de crecimiento'],
        limitations: 'Cálculo más complejo. Comportamiento similar a NDVI en vegetación densa.',
        color: '#f97316'
    },
    GNDVI: {
        id: 'GNDVI',
        name: 'GNDVI',
        fullName: 'NDVI Verde',
        description: 'Variante del NDVI usando la banda verde. Más sensible a concentraciones de clorofila.',
        formula: '(NIR - GREEN) / (NIR + GREEN)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< 0', meaning: 'Sin vegetación' },
            low: { range: '0 - 0.2', meaning: 'Vegetación escasa' },
            medium: { range: '0.2 - 0.4', meaning: 'Contenido de clorofila moderado' },
            high: { range: '0.4 - 0.6', meaning: 'Alta concentración de clorofila' },
            veryHigh: { range: '> 0.6', meaning: 'Muy alta actividad fotosintética' }
        },
        bestFor: ['Estimación de nitrógeno', 'Aplicación variable de fertilizantes', 'Cultivos de hoja'],
        limitations: 'Menos común en literatura. Puede ser más ruidoso que NDVI.',
        color: '#84cc16'
    }
};

export const getIndexInfo = (indexType: string): IndexInfo | undefined => {
    return INDEX_INFO[indexType.toUpperCase()];
};

export const getAllIndices = (): IndexInfo[] => {
    return Object.values(INDEX_INFO);
};
