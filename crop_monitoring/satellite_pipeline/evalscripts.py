"""Sentinel Hub evalscripts — SCL-masked Statistical API (v3) + Process paths."""

# --- Pixel mask: SCL + dataMask (QA60 bit test when BQA available — optional input) ---
# Outputs per-pixel quality flags aggregated by Statistical API (mean ≈ fraction).

STATISTICAL_S2_INDICES_MASKED_V3 = """
//VERSION=3
function setup() {
  return {
    input: ["B02","B03","B04","B05","B06","B08","B11","SCL","dataMask"],
    output: [
      { id: "indices", bands: 11, sampleType: "FLOAT32" },
      { id: "quality", bands: 4, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}
function isRejectedScl(scl) {
  return scl === 0 || scl === 1 || scl === 3 || scl === 8 || scl === 9 || scl === 10 || scl === 11;
}
function evaluatePixel(sample) {
  var scl = sample.SCL;
  var cloud = 0, shadow = 0, rejected = 0, clear = 1;
  if (isRejectedScl(scl)) {
    clear = 0;
    if (scl === 3) shadow = 1;
    else if (scl === 8 || scl === 9 || scl === 10) cloud = 1;
    else rejected = 1;
  }
  if (!sample.dataMask) { clear = 0; rejected = 1; }
  var mask = clear;
  var B02=sample.B02,B03=sample.B03,B04=sample.B04,B05=sample.B05,B06=sample.B06,B08=sample.B08,B11=sample.B11;
  var ndvi=0,savi=0,ndmi=0,ndre=0,gci=0,psri=0,msavi=0,evi=0,ndwi=0,gndvi=0,msi=0;
  if (mask) {
    var d_nr=B08+B04;
    if(d_nr>0){
      ndvi=(B08-B04)/d_nr;
      savi=((B08-B04)/(d_nr+0.5))*1.5;
      gndvi=(B08-B03)/(B08+B03);
      var twoNIR=2*B08+1,disc=twoNIR*twoNIR-8*(B08-B04);
      msavi=disc>=0?(twoNIR-Math.sqrt(disc))/2:0;
      var denom=B08+6*B04-7.5*B02+1;
      evi=denom!==0?2.5*(B08-B04)/denom:0;
    }
    var d_ns=B08+B11;
    if(d_ns>0) { ndmi=(B08-B11)/d_ns; msi=B11/B08; }
    var d_re=B08+B05;
    if(d_re>0) ndre=(B08-B05)/d_re;
    if(B03>0) gci=(B08/B03)-1;
    if(B08>0) psri=(B04-B03)/B08;
    var d_gw=B03+B08+1e-8;
    if(d_gw>0) ndwi=Math.max(-1,Math.min(1,(B03-B08)/d_gw));
  }
  return {
    indices: [ndvi,savi,ndmi,ndre,gci,psri,msavi,evi,ndwi,gndvi,msi],
    quality: [clear, cloud, shadow, rejected],
    dataMask: [mask]
  };
}
"""

STATISTICAL_S2_BANDS_MASKED_V3 = """
//VERSION=3
function setup() {
  return {
    input: ["B01","B02","B03","B04","B05","B06","B07","B08","B8A","B09","B11","B12","SCL","dataMask"],
    output: [
      { id: "bands", bands: 12, sampleType: "FLOAT32" },
      { id: "quality", bands: 4, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}
function isRejectedScl(scl) {
  return scl === 0 || scl === 1 || scl === 3 || scl === 8 || scl === 9 || scl === 10 || scl === 11;
}
function evaluatePixel(s) {
  var scl = s.SCL;
  var cloud=0,shadow=0,rejected=0,clear=1;
  if(isRejectedScl(scl)){
    clear=0;
    if(scl===3) shadow=1;
    else if(scl===8||scl===9||scl===10) cloud=1;
    else rejected=1;
  }
  if(!s.dataMask){clear=0;rejected=1;}
  var m=clear;
  return {
    bands: m ? [s.B01,s.B02,s.B03,s.B04,s.B05,s.B06,s.B07,s.B08,s.B8A,s.B09,s.B11,s.B12] : [0,0,0,0,0,0,0,0,0,0,0,0],
    quality: [clear, cloud, shadow, rejected],
    dataMask: [m]
  };
}
"""

S2_INDEX_NAMES_V3 = [
    "NDVI", "SAVI", "NDMI", "NDRE", "GCI", "PSRI", "MSAVI", "EVI", "NDWI", "GNDVI", "MSI",
]
S2_BAND_IDS_V3 = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12"]
QUALITY_BAND_NAMES = ["clear", "cloud", "shadow", "rejected"]

# Legacy v2 (kept for reprocess of old raw rows)
STATISTICAL_S2_BANDS_V2 = """
//VERSION=3
function setup() {
  return {
    input: ["B01","B02","B03","B04","B05","B06","B07","B08","B8A","B09","B11","B12","dataMask"],
    output: [
      { id: "bands", bands: 12, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}
function evaluatePixel(s) {
  return {
    bands: [s.B01,s.B02,s.B03,s.B04,s.B05,s.B06,s.B07,s.B08,s.B8A,s.B09,s.B11,s.B12],
    dataMask: [s.dataMask]
  };
}
"""

STATISTICAL_S2_INDICES_V2 = """
//VERSION=3
function setup() {
  return {
    input: ["B02","B03","B04","B05","B06","B08","B11","dataMask"],
    output: [
      { id: "indices", bands: 9, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}
function evaluatePixel(sample) {
  var B02=sample.B02,B03=sample.B03,B04=sample.B04,B05=sample.B05,B06=sample.B06,B08=sample.B08,B11=sample.B11;
  var ndvi=0,savi=0,ndmi=0,ndre=0,gci=0,psri=0,msavi=0,evi=0,ndwi=0;
  var d_nr=B08+B04;
  if(d_nr>0){
    ndvi=(B08-B04)/d_nr;
    savi=((B08-B04)/(d_nr+0.5))*1.5;
    var twoNIR=2*B08+1,disc=twoNIR*twoNIR-8*(B08-B04);
    msavi=disc>=0?(twoNIR-Math.sqrt(disc))/2:0;
    var denom=B08+6*B04-7.5*B02+1;
    evi=denom!==0?2.5*(B08-B04)/denom:0;
  }
  var d_ns=B08+B11;
  if(d_ns>0) ndmi=(B08-B11)/d_ns;
  var d_re=B08+B05;
  if(d_re>0) ndre=(B08-B05)/d_re;
  if(B03>0) gci=(B08/B03)-1;
  if(B08>0) psri=(B04-B03)/B08;
  var d_gw=B03+B08+1e-8;
  if(d_gw>0) ndwi=Math.max(-1,Math.min(1,(B03-B08)/d_gw));
  return { indices:[ndvi,savi,ndmi,ndre,gci,psri,msavi,evi,ndwi], dataMask:[sample.dataMask] };
}
"""

S2_INDEX_NAMES_V2 = ["NDVI", "SAVI", "NDMI", "NDRE", "GCI", "PSRI", "MSAVI", "EVI", "NDWI"]
S2_BAND_IDS_V2 = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12"]

PROCESS_S3_THERMAL_V2 = """
//VERSION=3
function setup() {
  return { input: ["S7","S8","S9"], output: { bands: 3, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [s.S7, s.S8, s.S9]; }
"""

PROCESS_S1_GRD_V2 = """
//VERSION=3
function setup() {
  return { input: ["VV","VH"], output: { bands: 2, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [s.VV, s.VH]; }
"""
