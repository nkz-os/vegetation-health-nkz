//VERSION=3
// Multi-index evalscript for Sentinel Hub Statistical API.
// Computes NDVI, EVI, SAVI, GNDVI, NDRE in a single request.
// Returns one band per index for zonal statistics.

function setup() {
  return {
    input: [{
      bands: ["B02", "B03", "B04", "B05", "B08", "B8A", "SCL", "dataMask"],
      units: "reflectance"
    }],
    output: [
      { id: "ndvi",  bands: 1, sampleType: "FLOAT32" },
      { id: "evi",   bands: 1, sampleType: "FLOAT32" },
      { id: "savi",  bands: 1, sampleType: "FLOAT32" },
      { id: "gndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "ndre",  bands: 1, sampleType: "FLOAT32" },
    ],
    mosaicking: "ORBIT"
  };
}

// SCL-based cloud mask: exclude clouds, shadows, cirrus, snow
function isClear(sample) {
  var scl = sample.SCL;
  // 0=NO_DATA, 1=SATURATED, 3=CLOUD_SHADOWS, 8=CLOUD_MEDIUM,
  // 9=CLOUD_HIGH, 10=THIN_CIRRUS, 11=SNOW
  var bad = [0, 1, 3, 8, 9, 10, 11];
  return bad.indexOf(scl) === -1 && sample.dataMask === 1;
}

const L = 0.5;  // SAVI soil adjustment factor

function evaluatePixel(samples) {
  if (!isClear(samples)) {
    return { ndvi: [NaN], evi: [NaN], savi: [NaN], gndvi: [NaN], ndre: [NaN] };
  }

  var b02 = samples.B02;  // Blue
  var b03 = samples.B03;  // Green
  var b04 = samples.B04;  // Red
  var b05 = samples.B05;  // Red Edge 1
  var b08 = samples.B08;  // NIR
  var b8a = samples.B8A;  // Red Edge 4 (narrow NIR)

  // Prevent division by zero
  var ndvi = (b08 + b04) !== 0 ? (b08 - b04) / (b08 + b04) : NaN;
  var evi  = (b08 + 6 * b04 - 7.5 * b02 + 1) !== 0
    ? 2.5 * (b08 - b04) / (b08 + 6 * b04 - 7.5 * b02 + 1) : NaN;
  var savi = (b08 + b04 + L) !== 0
    ? (b08 - b04) / (b08 + b04 + L) * (1 + L) : NaN;
  var gndvi = (b08 + b03) !== 0 ? (b08 - b03) / (b08 + b03) : NaN;
  var ndre  = (b8a + b05) !== 0 ? (b8a - b05) / (b8a + b05) : NaN;

  return {
    ndvi:  [ndvi],
    evi:   [evi],
    savi:  [savi],
    gndvi: [gndvi],
    ndre:  [ndre],
  };
}
