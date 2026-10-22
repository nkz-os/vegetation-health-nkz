//VERSION=3
// NDMI evalscript for the Sentinel Hub Statistical API.
// Kept apart from multi_index.js so only NDMI requests pay for the SWIR band.
// NDMI = (B8A - B11) / (B8A + B11): narrow NIR against SWIR 1, both native 20 m.

function setup() {
  return {
    input: [{
      // SCL is categorical and only served in DN; the spectral bands stay reflectance.
      bands: ["B8A", "B11", "SCL", "dataMask"],
      units: ["reflectance", "reflectance", "DN", "DN"]
    }],
    output: [
      { id: "ndmi", bands: 1, sampleType: "FLOAT32" },
      // Required by the Statistical API to drop invalid/cloudy pixels.
      { id: "dataMask", bands: 1 },
    ],
    // SIMPLE: one sample per pixel (least cloudy in the interval).
    mosaicking: "SIMPLE"
  };
}

// SCL-based cloud mask: exclude no data, saturated, shadows, clouds, cirrus, snow.
function isClear(sample) {
  var bad = [0, 1, 3, 8, 9, 10, 11];
  return bad.indexOf(sample.SCL) === -1 && sample.dataMask === 1;
}

function evaluatePixel(samples) {
  if (!isClear(samples)) {
    return { ndmi: [NaN], dataMask: [0] };
  }
  var b8a = samples.B8A;  // narrow NIR
  var b11 = samples.B11;  // SWIR 1
  var ndmi = (b8a + b11) !== 0 ? (b8a - b11) / (b8a + b11) : NaN;
  return { ndmi: [ndmi], dataMask: [1] };
}
