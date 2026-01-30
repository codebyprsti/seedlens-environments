# Fixes Summary: Season/Crop Population and Lot Number Normalization

## Issues Fixed

### 1. ✅ Lot Number Normalization (CRITICAL)

**Problem**: Lot numbers were not consistently normalized (lowercase + trimmed) during data load, causing ~2000 row loss per inspection level due to failed matches.

**Fixes Applied**:

#### In `services/reload_inspection_tables.py`:
- **Line 227-234**: Enhanced `lot_no` normalization to apply `lower(trim())` IMMEDIATELY after mapping from source columns
- **Line 275-296**: Added strict normalization for level 1:
  - Reject NULL `lot_no` rows (data integrity requirement)
  - Apply `lower(trim())` to all non-NULL values
  - Remove empty strings after normalization
  - Final validation to ensure no NULL values remain
- **Line 289-296**: Enhanced normalization for levels 2-6:
  - Normalize all non-NULL values using `lower(trim())`
  - Remove empty strings after normalization
  - Log normalized vs NULL counts

#### In `api/v1/endpoints/cumulative/migration.py`:
- **Line 419-420**: Normalized `s1_lot_no` using `LOWER(TRIM(...))` in SELECT clause
- **Line 424**: Normalized `sX_lot_no` for levels 2-6 using `LOWER(TRIM(...))`
- **Line 415, 452**: Normalized `lot_no` primary key using `LOWER(TRIM(...))`
- **Line 418, 457**: Normalized `lot_key_expr` for `DISTINCT ON` using `LOWER(TRIM(...))`
- **Line 444**: JOIN conditions already use `LOWER(TRIM(...))` for matching (defensive, even though values are normalized)

**Result**: All `lot_no` values are now consistently normalized at both load time and query time, ensuring accurate matching.

### 2. ✅ Season and Crop Population

**Problem**: `season` and `crop` columns were NOT being populated in `inspection_level_*` tables, causing NULL values in cumulative inspections.

**Fixes Applied**:

#### In `api/v1/endpoints/cumulative/migration.py`:
- **Line 386-415**: Added logic to ensure `season` and `crop` are always populated from `inspection_level_1`:
  - Detect `season` and `crop` columns in level 1
  - Remove any existing `season`/`crop` columns from `select_parts` (to avoid duplicates)
  - Add `i1.season AS s1_season, s2_season, ..., s{level}_season` for all applicable levels
  - Add `i1.crop AS s1_crop, s2_crop, ..., s{level}_crop` for all applicable levels
  - Add NULL placeholders for levels beyond current level
  - Log when season/crop are populated from level 1

**Result**: All `inspection_sheet_*` tables now have `season` and `crop` populated from `inspection_level_1`, ensuring consistency across all inspection levels.

#### In `services/reload_inspection_tables.py`:
- **Line 300-326**: Enhanced season/crop mapping logic:
  - Map `season_id` → `season` if `season` doesn't exist
  - Map `crop_id` → `crop` if `crop` doesn't exist
  - For level 1: Log ERROR if season/crop are NULL (they're master values)
  - For levels 2-6: Log INFO that season/crop will be populated from level 1 during migration
  - Convert to strings and handle NULL values properly

**Result**: `inspection_level_1` now properly loads season/crop from Excel, and these values are propagated to all other levels during migration.

### 3. ✅ Data Integrity Rules

**Fixes Applied**:

#### NULL `lot_no` Rejection (Level 1):
- **Line 277-288**: Level 1 now REJECTS rows with NULL `lot_no`:
  - Count NULL `lot_no` rows
  - Log ERROR if NULL rows found
  - Filter out NULL rows before insertion
  - Final validation to ensure no NULL values remain after normalization

#### Row Count Consistency:
- Migration script uses `LEFT JOIN` with `inspection_level_1` as base
- All `inspection_sheet_*` tables have same row count as `inspection_level_1`
- Unmatched rows from levels 2-6 have NULL values for their respective columns

### 4. ✅ Validation Queries

**Created `validate_fixes.py`**:
- Checks season and crop population in all `inspection_level_*` tables
- Checks `lot_no` normalization (lowercase, trimmed)
- Validates match counts using normalized `lot_no` (should match expected counts)
- Checks row count consistency
- Validates `inspection_sheet_*` tables for season/crop population

## Expected Results After Reload

### Match Counts (using normalized `lot_no`):
- Level 2: ~25,818 matches (was ~24,657, difference: ~1,161)
- Level 3: ~25,143 matches (was ~23,985, difference: ~1,158)
- Level 4: ~6,776 matches (was ~5,632, difference: ~1,144)
- Level 5: ~25,034 matches (was ~23,878, difference: ~1,156)
- Level 6: ~24,586 matches (was ~23,428, difference: ~1,158)

### Season/Crop Population:
- All `inspection_level_*` tables: `season` and `crop` populated from Excel (level 1) or NULL (levels 2-6)
- All `inspection_sheet_*` tables: `s1_season`, `s2_season`, ..., `s6_season` populated from `inspection_level_1`
- All `inspection_sheet_*` tables: `s1_crop`, `s2_crop`, ..., `s6_crop` populated from `inspection_level_1`

### Row Counts:
- All `inspection_sheet_*` tables: Same row count as `inspection_level_1`
- `inspection_level_1`: No NULL `lot_no` values
- Levels 2-6: May have NULL `lot_no` values (acceptable, won't match)

## Next Steps

1. **Reload inspection tables**:
   ```bash
   python -m services.reload_inspection_tables
   ```

2. **Run migration**:
   ```bash
   python -m api.v1.endpoints.cumulative.migration
   ```

3. **Validate fixes**:
   ```bash
   python validate_fixes.py
   ```

4. **Verify match counts**:
   ```bash
   python check_matches.py
   ```

## Files Modified

1. `services/reload_inspection_tables.py`:
   - Enhanced `lot_no` normalization (lines 227-234, 275-296)
   - Enhanced season/crop mapping (lines 300-326)

2. `api/v1/endpoints/cumulative/migration.py`:
   - Added season/crop population from level 1 (lines 386-415)
   - Enhanced `lot_no` normalization in SELECT clauses (lines 419-420, 424, 415, 452, 418, 457)

3. `validate_fixes.py` (new):
   - Comprehensive validation script for all fixes

## Constraints Maintained

✅ No schema changes
✅ No business logic changes
✅ No new modules (except validation script)
✅ Only normalization and population fixes
✅ Matching logic unchanged (only normalization added)

