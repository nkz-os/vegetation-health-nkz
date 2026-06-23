/**
 * Information about vegetation indices for educational display
 * All descriptions in English (i18n keys will be added in a future pass)
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
        fullName: 'Normalized Difference Vegetation Index',
        description: 'The most widely used vegetation index for measuring greenness and vigor. Detects active chlorophyll in plants.',
        formula: '(NIR - RED) / (NIR + RED)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< 0', meaning: 'Water, clouds or snow' },
            low: { range: '0 - 0.2', meaning: 'Bare soil, rock, or urban areas' },
            medium: { range: '0.2 - 0.4', meaning: 'Sparse or stressed vegetation' },
            high: { range: '0.4 - 0.6', meaning: 'Moderate vegetation, growing crops' },
            veryHigh: { range: '> 0.6', meaning: 'Dense, healthy vegetation' }
        },
        bestFor: ['Extensive crops (cereals, corn)', 'General health monitoring', 'Pastures and grasslands', 'Stress detection'],
        limitations: 'Can saturate in very dense vegetation (forests). Sensitive to atmospheric effects.',
        color: '#22c55e'
    },
    EVI: {
        id: 'EVI',
        name: 'EVI',
        fullName: 'Enhanced Vegetation Index',
        description: 'Improved version of NDVI that corrects atmospheric distortions and reduces saturation in high biomass areas.',
        formula: '2.5 × (NIR - RED) / (NIR + 6×RED - 7.5×BLUE + 1)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< 0', meaning: 'No vegetation' },
            low: { range: '0 - 0.2', meaning: 'Very sparse vegetation' },
            medium: { range: '0.2 - 0.4', meaning: 'Moderate vegetation' },
            high: { range: '0.4 - 0.6', meaning: 'Dense vegetation' },
            veryHigh: { range: '> 0.6', meaning: 'Very dense vegetation (forests)' }
        },
        bestFor: ['Dense forests', 'Tropical zones', 'High biomass areas', 'Forestry studies'],
        limitations: 'Requires blue band (B02). More complex to compute.',
        color: '#16a34a'
    },
    NDWI: {
        id: 'NDWI',
        name: 'NDWI',
        fullName: 'Normalized Difference Water Index',
        description: 'Detects water content in vegetation using NIR and SWIR bands. Essential for irrigation management.',
        formula: '(NIR - SWIR1) / (NIR + SWIR1)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< -0.3', meaning: 'Severe water deficit' },
            low: { range: '-0.3 - 0', meaning: 'Water-stressed vegetation' },
            medium: { range: '0 - 0.2', meaning: 'Normal water content' },
            high: { range: '0.2 - 0.4', meaning: 'High water content' },
            veryHigh: { range: '> 0.4', meaning: 'Very hydrated vegetation or water' }
        },
        bestFor: ['Water stress detection', 'Irrigation management', 'Drought monitoring', 'Irrigation planning'],
        limitations: 'Requires SWIR band (20m resolution). Sensitive to moist soil.',
        color: '#3b82f6'
    },
    NDMI: {
        id: 'NDMI',
        name: 'NDMI',
        fullName: 'Normalized Difference Moisture Index',
        description: 'Measures moisture content in vegetation. More sensitive to water stress than NDVI. Excellent for detecting irrigation issues.',
        formula: '(NIR - SWIR1) / (NIR + SWIR1)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< -0.3', meaning: 'Severe moisture deficit' },
            low: { range: '-0.3 - 0', meaning: 'Moisture-stressed vegetation' },
            medium: { range: '0 - 0.2', meaning: 'Normal moisture' },
            high: { range: '0.2 - 0.4', meaning: 'Good moisture content' },
            veryHigh: { range: '> 0.4', meaning: 'Very high moisture' }
        },
        bestFor: ['Irrigation monitoring', 'Drought assessment', 'Crop water status', 'Irrigation efficiency'],
        limitations: 'Similar to NDWI. Requires SWIR band. Can be affected by soil background.',
        color: '#06b6d4'
    },
    SAVI: {
        id: 'SAVI',
        name: 'SAVI',
        fullName: 'Soil Adjusted Vegetation Index',
        description: 'NDVI variant that corrects for soil background influence. Ideal for areas with partial vegetation cover and visible soil.',
        formula: '(NIR - RED) × (1 + L) / (NIR + RED + L)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< 0', meaning: 'No vegetation' },
            low: { range: '0 - 0.2', meaning: 'Very sparse cover' },
            medium: { range: '0.2 - 0.4', meaning: 'Moderate cover with soil visible' },
            high: { range: '0.4 - 0.6', meaning: 'Good cover' },
            veryHigh: { range: '> 0.6', meaning: 'Dense vegetation' }
        },
        bestFor: ['Arid and semi-arid zones', 'Early crop stages', 'Partial cover areas', 'No-till farming'],
        limitations: 'Requires soil brightness factor (L). Less used than NDVI.',
        color: '#eab308'
    },
    GNDVI: {
        id: 'GNDVI',
        name: 'GNDVI',
        fullName: 'Green Normalized Difference Vegetation Index',
        description: 'NDVI variant using green band instead of red. More sensitive to chlorophyll content and nitrogen status.',
        formula: '(NIR - GREEN) / (NIR + GREEN)',
        range: [-1, 1],
        interpretation: {
            veryLow: { range: '< 0', meaning: 'No vegetation' },
            low: { range: '0 - 0.2', meaning: 'Low chlorophyll' },
            medium: { range: '0.2 - 0.35', meaning: 'Moderate chlorophyll' },
            high: { range: '0.35 - 0.5', meaning: 'Good chlorophyll content' },
            veryHigh: { range: '> 0.5', meaning: 'High chlorophyll, good nitrogen' }
        },
        bestFor: ['Nitrogen status assessment', 'Fertilization planning', 'Chlorophyll estimation', 'Precision fertilization'],
        limitations: 'Sensitive to atmospheric conditions. Lower dynamic range than NDVI.',
        color: '#84cc16'
    },
};

export const getIndexInfo = (indexType: string): IndexInfo | undefined => {
    return INDEX_INFO[indexType.toUpperCase()];
};

export const getAllIndices = (): IndexInfo[] => {
    return Object.values(INDEX_INFO);
};
