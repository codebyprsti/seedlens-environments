# Planning Methods Flow Documentation

## Overview
This document describes the execution flow for Planning Methods APIs, from UI request to plan generation.

---

## Flow Architecture

```
UI Request → endpoints.py → plan_execution.py → supply_chain_planning_service.py
```

---

## 1. API Endpoints (`endpoints.py`)

### `/dropdown` (GET)
**Purpose**: Cascading dropdown for Planning Methods → Display screen

**Flow**:
1. No params → Returns static plan versions list
2. `plan_revision_version` → Returns seasons (from `operations.supply_chain_planning`)
3. `plan_revision_version` + `season_id` → Returns crops
4. `plan_revision_version` + `season_id` + `crop_id` → Returns states (from `operations.targets`)
5. `plan_revision_version` + `season_id` + `crop_id` + `state` → Returns varieties (from `operations.supply_chain_planning`)
6. `plan_revision_version` + `season_id` + `crop_id` + `state` + `variety_id` → Returns single target (from `operations.targets`)

**Key**: Uses `operations.targets` as single source of truth for targets.

---

### `/methods` (GET)
**Purpose**: Returns available planning methods and their criteria

**Flow**:
- No param → Returns list of methods: `["maximization", "minimization", "min_max"]`
- `?method=<methodId>` → Returns method details with criteria configuration

---

### `/execute` (POST)
**Purpose**: Execute planning algorithm with UI selections

**Request Payload**:
```json
{
  "plan_revision_version": "v1.0-Maximized-Productivity",
  "season_id": "RABI_24_25",
  "crop_id": "CR_001",
  "targets": [
    {"state": "Odisha", "variety_id": "VR_1001", "quantity": 50000}
  ],
  "method_algorithm": "Maximization",
  "criteria": [{"id": "Productivity", "min_allocation": 0, "max_allocation": 50000}],
  "constraints": [{"id": "Target_Budget", "value": 50000}]
}
```

**Flow**:
1. Validates request payload
2. Calls `PlanExecutionOrchestrator.execute_plan()`
3. Formats response with columns metadata and data rows
4. Returns JSON response

---

## 2. Orchestration Layer (`plan_execution.py`)

### `PlanExecutionOrchestrator.execute_plan()`

**Purpose**: Coordinates between API and planning services

**Steps**:

1. **Extract Year Range**
   - Parses `plan_revision_version` or `season_id` to get `YYYY-YYYY` format
   - Example: `"RABI_24_25"` → `"2024-2025"`

2. **Build State-Variety Targets** (`_build_state_variety_targets`)
   - Fetches states from `operations.targets` table (single source of truth)
   - Validates payload states match DB states (case-insensitive)
   - Converts `quantity` (Kgs) to MT
   - Returns: `{state: {variety_id: target_MT}}`

3. **Route to Planning Service**
   - `"maximization"` → `SupplyChainPlanner` (supply_chain_planning_service.py)
   - `"minimization"` → `MinimizationPlanner` (minimization_planning_service.py)
   - `"min-max"` → `SupplyChainPlanner` (Linear_programming.py)

4. **Call Service**
   - Calls `planner.execute_with_ui_selections()` with:
     - `state_variety_targets`: Overridden targets from UI
     - `season_id`, `crop_id`: For plan year extraction
     - `criteria`, `constraints`: Configuration

5. **Return DataFrame**
   - Returns planning results as pandas DataFrame

---

## 3. Planning Service (`supply_chain_planning_service.py`)

### `execute_with_ui_selections()`

**Purpose**: Execute planning with UI payload targets (NO data filtering, NO DB/CSV saves)

**Steps**:

1. **Extract Plan Year**
   - Parses `season_id` to get plan year (e.g., `"RABI_24_25"` → `2024`)

2. **Convert Variety IDs to Names**
   - Queries `operations.varieties` to map `variety_id` → `variety_name`
   - Normalizes to USRH codes (e.g., `"USRH-05"`)

3. **Override Targets**
   - Replaces hardcoded `self.state_variety_targets` with UI payload
   - Converts `target_MT` to `target_Kgs` (MT × 1000)
   - Sets `self.targets_set_from_ui = True` (prevents `generate_plan()` from overwriting)

4. **Disable Saves**
   - Monkey-patches `save_plan_to_database()` → returns `True` (no-op)
   - Sets `self.output_folder = tempfile.gettempdir()` (prevents CSV saves)
   - Monkey-patches `print_state_variety_statistics()` → prints to terminal only

5. **Generate Plan**
   - Calls `generate_plan(plan_year)` which:
     - Skips `set_targets_for_plan_year()` (targets already set from UI)
     - Loads ALL data from `operations.season_crop_inspection_final` (NO filtering)
     - Generates village summary (Y0 + Y1 data)
     - Calls `allocate_target_production_max()` with UI targets
     - Returns DataFrame with allocation results

6. **Allocation Logic** (`allocate_target_production_max()`)
   - **For each state-variety target**:
     - Filters villages by state (normalized matching)
     - Filters villages by variety (USRH code matching)
     - Sorts by productivity (descending) - highest first
     - Allocates target production starting with highest productivity village
     - Continues until target met or all villages used
   - **Output**: Updates `village_summary` DataFrame with:
     - `allocated_acres`
     - `adjusted_production_allocation` (Kgs)
     - `probable_cost`
     - `estimated_production_cost`

7. **Return Results**
   - Returns DataFrame with all villages (allocated + non-allocated)
   - Results printed to terminal (statistics, allocation summary)

8. **Cleanup**
   - Restores original save methods
   - Resets `targets_set_from_ui = False`

---

## Key Principles

### ✅ DO:
- Use UI payload targets as single source of truth
- Keep ALL allocation logic unchanged (productivity ranking, calculations)
- Load ALL data from database (no filtering of `self.data`)
- Print results to terminal for API execution
- Disable DB/CSV saves for API execution

### ❌ DON'T:
- Filter or modify `self.data` DataFrame
- Change productivity or allocation calculations
- Save to database when called from API
- Download CSV files when called from API
- Override targets if already set from UI

---

## Data Flow Summary

```
UI Payload (targets)
    ↓
plan_execution.py (_build_state_variety_targets)
    ↓
operations.targets (validate states, get variety mappings)
    ↓
supply_chain_planning_service.py (override self.state_variety_targets)
    ↓
generate_plan() (uses overridden targets)
    ↓
allocate_target_production_max() (same logic, different targets)
    ↓
DataFrame (results)
    ↓
endpoints.py (format response)
    ↓
JSON Response (columns + data)
```

---

## Execution Modes

### 1. Direct Execution (`main()`)
- Uses hardcoded targets from `state_variety_targets_2024` / `state_variety_targets_2025`
- Saves to database (`operations.supply_chain_planning`)
- Downloads CSV files
- Prints statistics to terminal

### 2. API Execution (`execute_with_ui_selections()`)
- Uses UI payload targets (overrides hardcoded)
- NO database saves
- NO CSV downloads
- Prints results to terminal only

---

## Database Tables Used

1. **`operations.season_crop_inspection_final`**
   - Source data for planning (village, variety, productivity, cost, etc.)
   - Loaded via `load_from_database()` → `self.data`

2. **`operations.targets`**
   - Single source of truth for target production
   - Columns: `plan_year`, `season_id`, `crop_id`, `state`, `variety_id`, `target_kgs`
   - Used to validate and derive states for UI payload

3. **`operations.supply_chain_planning`**
   - Stores generated plans (only in direct execution mode)
   - NOT used during API execution

4. **`operations.varieties`**
   - Maps `variety_id` → `variety_name` (USRH codes)

5. **`operations.seasons`**
   - Maps `season_id` → `season_name`

6. **`operations.crops`**
   - Maps `crop_id` → `crop_name`

---

## Response Format

```json
{
  "columns": [
    {
      "db_column_name": "state",
      "display_name": "State",
      "type": "string",
      "group_name": "Location",
      "is_visible": true,
      "is_editable": false,
      "custom_order": 1
    },
    ...
  ],
  "data": [
    {
      "state": "Odisha",
      "village": "VILLAGE_NAME",
      "variety": "USRH-05",
      "crop": "Rice",
      "allocated_acres": 10.5,
      "adjusted_production_allocation": 5000.0
    },
    ...
  ],
  "plan_revision_version": "v1.0-Maximized-Productivity",
  "season": "RABI 24-25",
  "season_id": "RABI_24_25",
  "crop": "Rice",
  "crop_id": "CR_001",
  "record_count": 150
}
```

---

## Notes

- **No Data Filtering**: `self.data` contains ALL data from database. Allocation logic filters at village_summary level only.
- **Target Override**: UI payload completely replaces hardcoded targets. Allocation runs for ONLY the state-variety combinations in payload.
- **Same Logic**: Productivity ranking, cost calculations, and allocation algorithms remain unchanged.
- **Terminal Output**: All execution results (statistics, allocations) are printed to terminal for debugging/monitoring.

