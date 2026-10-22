/**
 * Helper to safely extract geometry handling both Normalized and Simplified NGSI-LD formats
 */
export const getEntityGeometry = (entity: any): any | null => {
    if (!entity) return null;

    // Standard GeoJSON at root (typical for Parcel objects in app context)
    if (entity.geometry && entity.geometry.type) return entity.geometry;

    // Normalized NGSI-LD
    if (entity.location?.value && entity.location.value.type) return entity.location.value;

    // Simplified NGSI-LD (KeyValues)
    if (entity.location && entity.location.type) return entity.location;

    return null;
};
