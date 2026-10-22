//VERSION=3
// Single-index FLOAT32 raster for the Sentinel Hub Process API.
// The target index is injected server-side at __INDEX__, and its input bands
// at __BANDS__/__UNITS__ (only the bands the index uses, plus SCL and dataMask).
function setup() {
  return {
    input: [{
      bands: __BANDS__,
      units: __UNITS__
    }],
    output: [
      { id: "default",  bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ],
    mosaicking: "SIMPLE"
  };
}
function isClear(s) {
  var bad = [0,1,3,8,9,10,11];
  return bad.indexOf(s.SCL) === -1 && s.dataMask === 1;
}
var L = 0.5;
function computeIndex(s) {
  var b02=s.B02,b03=s.B03,b04=s.B04,b05=s.B05,b08=s.B08,b8a=s.B8A,b11=s.B11;
  switch ("__INDEX__") {
    case "NDVI":  return (b08+b04)!==0 ? (b08-b04)/(b08+b04) : NaN;
    case "EVI":   return (b08+6*b04-7.5*b02+1)!==0 ? 2.5*(b08-b04)/(b08+6*b04-7.5*b02+1) : NaN;
    case "SAVI":  return (b08+b04+L)!==0 ? (b08-b04)/(b08+b04+L)*(1+L) : NaN;
    case "OSAVI": return (b08+b04+0.16)!==0 ? (b08-b04)/(b08+b04+0.16) : NaN;
    case "GNDVI": return (b08+b03)!==0 ? (b08-b03)/(b08+b03) : NaN;
    case "NDRE":  return (b8a+b05)!==0 ? (b8a-b05)/(b8a+b05) : NaN;
    case "NDMI":  return (b8a+b11)!==0 ? (b8a-b11)/(b8a+b11) : NaN;
    default:      return NaN;
  }
}
function evaluatePixel(s) {
  if (!isClear(s)) return { default: [NaN], dataMask: [0] };
  return { default: [computeIndex(s)], dataMask: [1] };
}
