# Sentinel API

## Endpoints

### GET /api/v1/sentinel/layer

Returns a single derived layer as GeoTIFF (NDVI, NDWI, true color, etc.).

**Query parameters:** `layer_name`, `min_lon`, `min_lat`, `max_lon`, `max_lat`, `date_from`, `date_to` (optional), `maxcc` (optional), `use_cache` (optional).

---

### GET /api/v1/sentinel/village-summary

Returns a CSV of layer statistics for the given village and bbox. Calls Sentinel once, then computes ndvi, ndwi, moisture_index, true_color, scl and their stats.

**Query parameters:** `village_name`, `district`, `state`, `min_lon`, `min_lat`, `max_lon`, `max_lat`, `date_from`, `date_to` (optional), `max_cloud_cover` (optional), `use_cache` (optional).

#### Example curl

```bash
curl -o village_summary.csv "http://localhost:8019/api/v1/sentinel/village-summary?village_name=Kompally&district=Medchal&state=Telangana&min_lon=78.48&min_lat=17.51&max_lon=78.50&max_lat=17.53&date_from=2026-02-01&date_to=2026-02-25&max_cloud_cover=20"
```

#### Example CSV output

```csv
village_name,district,state,min_lon,min_lat,max_lon,max_lat,date_from,date_to,layer_name,mean,min,max,std_dev,valid_pixel_count,bands,height,width,status
Kompally,Medchal,Telangana,78.48,17.51,78.5,17.53,2026-02-01,2026-02-25,ndvi,0.227733,-0.118083,0.909551,0.168005,47085,1,219,215,ok
Kompally,Medchal,Telangana,78.48,17.51,78.5,17.53,2026-02-01,2026-02-25,ndwi,-0.314864,-0.814303,0.355450,0.161424,47085,1,219,215,ok
Kompally,Medchal,Telangana,78.48,17.51,78.5,17.53,2026-02-01,2026-02-25,moisture_index,-0.046165,-0.345664,0.444461,0.067707,47085,1,219,215,ok
Kompally,Medchal,Telangana,78.48,17.51,78.5,17.53,2026-02-01,2026-02-25,true_color,,,,,,3,219,215,ok
Kompally,Medchal,Telangana,78.48,17.51,78.5,17.53,2026-02-01,2026-02-25,scl,4.972603,2.000000,7.000000,0.185869,47085,1,219,215,ok
```

For `true_color`, only `bands=3` is set; mean/min/max/std_dev/valid_pixel_count are left empty.
