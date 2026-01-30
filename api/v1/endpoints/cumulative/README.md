# Cumulative Inspection API - Performance Optimization

## Overview

This module provides optimized inspection data APIs that use pre-populated tables instead of dynamic JOINs for significantly better performance.

## Architecture

### Optimized Tables

The system uses 6 optimized tables (`inspection_sheet_1` through `inspection_sheet_6`) that contain pre-joined data with prefixed columns:

- **inspection_sheet_1**: Contains only `s1_` columns (from inspection_level_1)
- **inspection_sheet_2**: Contains `s1_` and `s2_` columns (matched by lot numbers)
- **inspection_sheet_3**: Contains `s1_`, `s2_`, and `s3_` columns (matched by lot numbers)
- ... and so on up to **inspection_sheet_6**

### Data Population Rules

1. **Inspection Sheet 1**: Only `s1_` columns populated, `s2_`-`s6_` are NULL
2. **Inspection Sheet 2**: `s1_` and `s2_` columns populated using common lot numbers
3. **Inspection Sheet 3**: `s1_`, `s2_`, `s3_` populated, matched by lot numbers
4. Pattern continues for sheets 4, 5, and 6

All lot number matching is done during data population, ensuring consistency.

## Migration

### Running the Migration

To create and populate the optimized tables:

```python
from api.v1.endpoints.cumulative.migration import migrate_to_optimized_tables

# Run migration
migrate_to_optimized_tables()
```

Or from command line:

```bash
python -m api.v1.endpoints.cumulative.migration
```

### What the Migration Does

1. **Gathers column information** from all 6 `inspection_level_*` tables
2. **Creates 6 optimized tables** (`inspection_sheet_1` through `inspection_sheet_6`) with:
   - All columns from level 1 prefixed with `s1_`
   - All columns from level 2 prefixed with `s2_` (if applicable)
   - ... up to `s6_`
   - A common `lot_no` column for joining
   - NULL columns for levels beyond the current sheet level
3. **Populates tables** by joining source tables and matching on lot numbers

### Migration Requirements

- All source tables (`inspection_level_1` through `inspection_level_6`) must exist
- Tables must have a lot number column (`lot_no`, `lot_id`, `lot_number`, or `mrno_lot_no`)
- Database connection must have CREATE TABLE and INSERT permissions

## API Usage

The API automatically uses optimized tables if they exist, falling back to JOIN-based queries if not.

### Endpoint

```
GET /api/v1/inspections/sheet/{level}?limit=100&offset=0
```

### Parameters

- `level` (path): Inspection level (1-6)
- `limit` (query): Maximum records to return (1-1000, default: 100)
- `offset` (query): Number of records to skip (default: 0)

### Response Format

```json
{
  "inspection_level": 3,
  "total_lots": 100,
  "limit": 100,
  "offset": 0,
  "has_more": true,
  "data": [
    {
      "s1_lot_no": "12345",
      "s1_inspection_date": "2024-01-15T10:30:00",
      "s2_lot_no": "12345",
      "s2_inspection_date": "2024-01-20T14:00:00",
      "s3_lot_no": "12345",
      "s3_inspection_date": "2024-01-25T16:00:00",
      "lot_no": "12345"
    }
  ]
}
```

## Performance Benefits

### Before Optimization (JOIN-based)
- Multiple table JOINs at query time
- Type casting and trimming for lot number matching
- Slower response times, especially for higher levels

### After Optimization (Pre-populated tables)
- Single table SELECT (no JOINs)
- Data already matched and prefixed
- **Significantly faster response times**

## Maintenance

### Refreshing Data

To refresh the optimized tables after source data changes:

```python
from api.v1.endpoints.cumulative.migration import migrate_to_optimized_tables

# This will recreate and repopulate all tables
migrate_to_optimized_tables()
```

### Monitoring

Check table sizes and row counts:

```sql
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size,
    n_live_tup AS row_count
FROM pg_stat_user_tables
WHERE tablename LIKE 'inspection_sheet_%'
ORDER BY tablename;
```

## Notes

- The optimized tables use `lot_no` as the primary key
- Data is matched using case-sensitive comparison with trimmed spaces
- The API maintains backward compatibility - if optimized tables don't exist, it falls back to JOIN-based queries
- Business logic and API responses remain exactly the same - only performance is improved

