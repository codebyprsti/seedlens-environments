# Sentinel-2 cloud handling (India / monsoon)

## Why tile-level 20% cloud filter fails

Copernicus scene metadata reports **whole-tile** cloud percentage. During the Indian monsoon, many useful scenes are labelled 30–60% cloudy while **field pixels remain clear**. Rejecting those scenes creates long gaps in NDVI time series.

## Production strategy (v3 pipeline)

1. **Scene search ceiling: 60%** (configurable via `--maxcc`, `S2_MAX_CLOUD_COVER`, or `copernicus-pipeline` settings).
2. **Pixel masking** in Statistical API evalscripts using **SCL** (and `dataMask`):
   - Excluded: no-data (0), saturated (1), cloud shadow (3), cloud medium/high (8–9), cirrus (10), snow (11).
3. **Indices computed only on clear pixels** (NDVI, EVI, NDRE, SAVI, GNDVI, NDWI, MSI, chlorophyll indices, etc.).
4. **Quality metadata** stored per day: `valid_pixel_percentage`, `cloud_pixel_percentage`, `shadow_pixel_percentage`, `usable_scene`, `quality_score`.
5. **Usability rule**: if valid pixels &lt; 30% (`S2_MIN_VALID_PIXEL_PCT`), `usable_scene=false` but **raw JSON is still stored** for reprocessing.

## Cloud fallback ladder

Per temporal chunk, the pipeline tries scene limits **20 → 40 → 60 → 80** until enough SCL-valid pixels exist (or ladder exhausted):

```text
Accept scene → mask clouds → aggregate clear-pixel means → check valid %
```

## Gap-aware fallback hierarchy

```text
Sentinel-2 (masked optical indices)
    ↓ if usable_scene=false
Sentinel-1 SAR (VV/VH, structure/moisture proxy)
    +
Sentinel-3 thermal (LST)
```

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `S2_MAX_CLOUD_COVER` | 60 | Scene catalogue / Hub pre-filter ceiling (%) |
| `S2_MIN_VALID_PIXEL_PCT` | 30 | Minimum clear pixels after SCL mask |
| `S2_CLOUD_FALLBACK_LADDER` | 20,40,60,80 | Escalation sequence |
| `S2_ALLOW_MAXCC_80` | true | Allow optional 80% scene fallback |

## Reprocess

`--mode reprocess_raw` recomputes indices from stored raw responses without API calls — supports new formulas and quality columns after schema upgrades.

## Migration

```bash
psql $DATABASE_URL -f sql/alter_sentinel2_cloud_quality.sql
```
