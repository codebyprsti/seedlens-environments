# Demo GeoJSON for villages near Hyderabad (Telangana, India)
from sentinelhub import (
    SHConfig, SentinelHubRequest, DataCollection, BBox, CRS, MimeType,
    Geometry, parse_time
)

village_polygons = {
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"village": "Kompally", "district": "Medchal", "state": "Telangana"},
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[78.48, 17.52], [78.49, 17.52], [78.49, 17.51], [78.48, 17.51], [78.48, 17.52]]]
      }
    },
    {
      "type": "Feature", 
      "properties": {"village": "Dundigal", "district": "Medchal", "state": "Telangana"},
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[78.46, 17.51], [78.47, 17.51], [78.47, 17.50], [78.46, 17.50], [78.46, 17.51]]]
      }
    }
  ]
}

# SHConfig for Copernicus Data Space (replace with your token)
config = SHConfig()
config.sh_client_id = "your_client_id"
config.sh_client_secret = "your_client_secret"
config.save()

# Parameters that affect output:
# 1. bbox/polygons: Clips data to village boundaries (determines spatial coverage)
# 2. resolution: 10m/20m/60m based on bands (affects file size/granularity)
# 3. time_interval: Date range (e.g., 2026-02-01 to 2026-02-25) - affects availability/cloud cover
# 4. maxcc: Cloud cover <20% - filters clear imagery
# 5. bands: B04(red), B03(green), B02(blue) for RGB; B08(NIR) for vegetation; B11, B12 for SWIR
# 6. evalscript: Custom processing (NDVI, moisture index)
# 7. mosaicking_order: LeastCC/LeastQA for best pixels
# Output factors: Village size → pixel count; Cloud cover → valid pixels; Time range → temporal composites

evalscript_rgb = """
//VERSION=3 (auto)
function setup() {
  return {
    input: ["B02", "B03", "B04", "SCL", "CLM"],
    output: { bands: 3, sampleType: "FLOAT32" }
  };
}

function evaluatePixel(sample) {
  let val = [0,0,0];
  if (sample.SCL && sample.SCL < 7 && sample.SCL > 3 && sample.CLM == 0) {
    val = [sample.B04*2.5, sample.B03*2.5, sample.B02*2.5];
  }
  return val;
}
"""

# Download RGB for villages (output: GeoTIFF clipped to polygons)
time_range = ("2026-02-01", "2026-02-25")
resolution = (10, 10)

request_rgb = SentinelHubRequest(
    evalscript=evalscript_rgb,
    input_data=[
        SentinelHubRequest.input_data(
            data_collection=DataCollection.SENTINEL2_L2A,
            time_interval=time_range,
            mosaicking_order="LEAST_CC"
        )
    ],
    responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
    # Use polygons as AOI
    geometry=Geometry(json=village_polygons, crs=CRS("EPSG:4326")),
    config=config,
    size=None  # Auto-size from geometry
)

rgb_data = request_rgb.get_data()[0]  # Outputs RGB GeoTIFF for villages

# NDVI evalscript example (add for vegetation analysis)
evalscript_ndvi = """
// NDVI for agricultural monitoring in villages
function setup() {
  return { input: ["B04", "B08"], output: { bands: 1 } };
}
function evaluatePixel(sample) {
  return [(sample.B08 - sample.B04) / (sample.B08 + sample.B04)];
}
"""

# Factors affecting NDVI output quality:
# - Village polygon accuracy → precise field boundaries
# - Cloud mask (SCL/CLM) → removes invalid pixels  
# - NIR band (B08) saturation → healthy vs stressed crops
# - Temporal resolution → growth stage composites
# - District-level aggregation → farm statistics

# Execute and save
request_ndvi = SentinelHubRequest(
    evalscript=evalscript_ndvi,
    input_data=[SentinelHubRequest.input_data(
        data_collection=DataCollection.SENTINEL2_L2A,
        time_interval=time_range,
        max_cloud_cover=20
    )],
    responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
    geometry=Geometry(json=village_polygons, crs=CRS("EPSG:4326")),
    config=config
)

ndvi_data = request_ndvi.get_data(save_data=True)  # Saves village_ndvi.tif

print("Downloaded RGB and NDVI for", [f["properties"]["village"] for f in village_polygons["features"]])
