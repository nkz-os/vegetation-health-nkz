//VERSION=3
// Visual NDVI tile evalscript for Sentinel Hub Process API.
// Produces an RGBA image with agronomic color ramp.

const COLOR_RAMP = [
  [-1.0, 0x440154],   // dark purple  — no vegetation
  [-0.2, 0x404788],   // blue
  [ 0.0, 0x238a8d],   // teal
  [ 0.1, 0x55c667],   // green
  [ 0.2, 0xa2d729],   // yellow-green
  [ 0.4, 0xfde725],   // yellow
  [ 0.6, 0xf9a825],   // orange
  [ 0.8, 0xe55525],   // red-orange
  [ 1.0, 0x9e0d0d],   // dark red — dense vegetation
];

function setup() {
  return {
    input: [{
      bands: ["B04", "B08", "SCL", "dataMask"],
      units: "reflectance"
    }],
    output: {
      bands: 4,
      sampleType: "UINT8"
    },
    mosaicking: "ORBIT"
  };
}

function isClear(sample) {
  var scl = sample.SCL;
  var bad = [0, 1, 3, 8, 9, 10, 11];
  return bad.indexOf(scl) === -1 && sample.dataMask === 1;
}

function interpolateColor(val) {
  if (isNaN(val)) return [0, 0, 0, 0];  // transparent for no-data

  // Clamp to ramp range
  if (val <= COLOR_RAMP[0][0]) {
    var c = COLOR_RAMP[0][1];
    return [(c >> 16) & 0xff, (c >> 8) & 0xff, c & 0xff, 255];
  }
  for (var i = 0; i < COLOR_RAMP.length - 1; i++) {
    if (val <= COLOR_RAMP[i + 1][0]) {
      var v0 = COLOR_RAMP[i][0];
      var v1 = COLOR_RAMP[i + 1][0];
      var t = (val - v0) / (v1 - v0);
      var c0 = COLOR_RAMP[i][1];
      var c1 = COLOR_RAMP[i + 1][1];
      var r = Math.round(((c0 >> 16) & 0xff) * (1 - t) + ((c1 >> 16) & 0xff) * t);
      var g = Math.round(((c0 >> 8) & 0xff) * (1 - t) + ((c1 >> 8) & 0xff) * t);
      var b = Math.round((c0 & 0xff) * (1 - t) + (c1 & 0xff) * t);
      return [r, g, b, 255];
    }
  }
  var clast = COLOR_RAMP[COLOR_RAMP.length - 1][1];
  return [(clast >> 16) & 0xff, (clast >> 8) & 0xff, clast & 0xff, 255];
}

function evaluatePixel(samples) {
  if (!isClear(samples)) {
    return [0, 0, 0, 0];  // transparent
  }
  var ndvi = (samples.B08 + samples.B04) !== 0
    ? (samples.B08 - samples.B04) / (samples.B08 + samples.B04) : NaN;
  return interpolateColor(ndvi);
}
