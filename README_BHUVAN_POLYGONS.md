# Bhuvan API Polygon Fetching Module

This module fetches village-level polygon (boundary) data from the Bhuvan API using administrative details stored in the database.

## Overview

The system reads unique location records from `operations.locations`, constructs API requests to Bhuvan, handles errors and retries, validates geometry, and stores polygon data in `operations.location_polygons` table.

## Components

### 1. Python Module
**File:** `services/bhuvan_polygon_service.py`

Main service class `BhuvanPolygonService` that handles:
- Reading unique records from `operations.locations`
- Constructing Bhuvan API requests
- HTTP error handling and retry logic (max 2 retries)
- Parsing and validating polygon geometry (GeoJSON)
- Storing results in database
- Preventing duplicate records

### 2. Database Schema
**File:** `sql/create_location_polygons_table.sql`

SQL script to create the `operations.location_polygons` table with:
- PostGIS geometry column for polygon storage
- Foreign key to `operations.locations`
- Indexes for performance
- Automatic timestamp updates

### 3. Database Model
**File:** `models/db_models.py`

SQLAlchemy model `LocationPolygonRecord` for ORM access to polygon data.

### 4. Execution Script
**File:** `scripts/fetch_bhuvan_polygons.py`

Command-line script to run the polygon fetching process with various options.

### 5. API Documentation
**File:** `docs/bhuvan_api_example.md`

Example API request formats and response structures.

## Setup

### 1. Database Setup

Run the SQL schema script to create the table:

```bash
psql -U your_user -d your_database -f sql/create_location_polygons_table.sql
```

Or execute the SQL directly in your PostgreSQL client.

**Prerequisites:**
- PostgreSQL with PostGIS extension enabled
- Access to `operations` schema
- Foreign key relationship to `operations.locations` table

### 2. Install Dependencies

Ensure you have the required Python packages:

```bash
pip install requests psycopg2 sqlalchemy
```

### 3. Configure Access Token

The access token is configured in `services/bhuvan_polygon_service.py`:

```python
ACCESS_TOKEN = "709492afd90de061e1a078ac40647b82b9bf8fa0"
```

You can also pass a custom token when initializing the service or via command-line argument.

## Usage

### Command-Line Script

Basic usage:

```bash
python scripts/fetch_bhuvan_polygons.py
```

With options:

```bash
# Process 50 records per batch
python scripts/fetch_bhuvan_polygons.py --batch-size 50

# Skip locations that already have polygons (default)
python scripts/fetch_bhuvan_polygons.py --skip-existing

# Process all locations, including existing ones
python scripts/fetch_bhuvan_polygons.py --no-skip-existing

# Dry run (show what would be done without API calls)
python scripts/fetch_bhuvan_polygons.py --dry-run

# Use custom access token
python scripts/fetch_bhuvan_polygons.py --access-token YOUR_TOKEN
```

### Python API Usage

```python
from sqlalchemy.orm import Session
from core.db import SessionLocal
from services.bhuvan_polygon_service import BhuvanPolygonService

# Initialize
db = SessionLocal()
service = BhuvanPolygonService(db, access_token="YOUR_TOKEN")

# Fetch polygons
stats = service.fetch_all_polygons(
    batch_size=100,
    skip_existing=True,
    dry_run=False
)

# Check results
print(f"Successful: {stats['successful']}")
print(f"Failed: {stats['failed']}")

# Cleanup
service.close()
db.close()
```

## Features

### 1. Unique Record Processing
- Uses `DISTINCT ON` to get unique (village, mandal, district, state) combinations
- Skips records where village or district is null
- Optionally skips locations that already have polygons

### 2. API Request Handling
- Text fields are trimmed and lowercased before API request
- Access token passed via header (Bearer) or query parameter
- Supports multiple response formats (Feature, FeatureCollection, direct geometry)

### 3. Error Handling & Retries
- Maximum 2 retries with exponential backoff
- Handles HTTP errors (401, 404, 429, 500, etc.)
- Handles timeouts and connection errors
- Logs all failures with detailed error messages

### 4. Geometry Validation
- Validates polygon structure (Polygon or MultiPolygon)
- Checks coordinate ranges (latitude: -90 to 90, longitude: -180 to 180)
- Ensures minimum 4 points for polygon rings
- Normalizes to standard GeoJSON format

### 5. Database Storage
- Uses PostGIS `ST_GeomFromGeoJSON` for geometry conversion
- Prevents duplicates via `ON CONFLICT DO NOTHING`
- Stores administrative details for quick access
- Maintains source identifier ('bhuvan')

### 6. Rate Limiting
- 0.5 second delay between API calls
- Respects API rate limits (429 handling)

## Logging

Logs are written to:
- **File:** `logs/bhuvan_polygon_fetch.log`
- **Console:** Standard output

Log levels:
- **INFO:** General progress and statistics
- **WARNING:** Skipped records, retries, API warnings
- **ERROR:** Failed requests, validation errors, database errors
- **DEBUG:** Detailed API request/response information

## Database Schema

### Table: `operations.location_polygons`

| Column | Type | Description |
|--------|------|-------------|
| `location_id` | VARCHAR(20) | Primary key, FK to operations.locations |
| `village` | VARCHAR(100) | Village name (denormalized) |
| `mandal` | VARCHAR(100) | Mandal name (optional) |
| `district` | VARCHAR(100) | District name |
| `state` | VARCHAR(100) | State name (optional) |
| `polygon_geom` | GEOMETRY | PostGIS geometry (Polygon/MultiPolygon, EPSG:4326) |
| `source` | VARCHAR(50) | Source identifier (default: 'bhuvan') |
| `created_at` | TIMESTAMP | Creation timestamp |
| `updated_at` | TIMESTAMP | Last update timestamp |

### Indexes
- Primary key on `location_id`
- Index on `village` (lowercase)
- Index on `district` (lowercase)
- Index on `state` (lowercase)
- Spatial GIST index on `polygon_geom`
- Composite index on (village, mandal, district, state)

## Constraints

1. **One polygon per location_id:** Enforced by primary key constraint
2. **No duplicates:** Uses `ON CONFLICT DO NOTHING` in INSERT
3. **Required fields:** Village and district must not be null
4. **Foreign key:** location_id must exist in operations.locations

## Error Handling

The service handles various error scenarios:

1. **API Errors:**
   - 401: Authentication failed (invalid token)
   - 404: Village not found
   - 429: Rate limit exceeded (retries with backoff)
   - 500+: Server errors (retries)

2. **Response Errors:**
   - Empty responses
   - Invalid JSON
   - Missing geometry in response
   - Invalid geometry structure

3. **Database Errors:**
   - Duplicate key violations (handled gracefully)
   - Connection errors
   - Transaction failures

All errors are logged with location details for troubleshooting.

## Performance Considerations

1. **Batch Processing:** Processes records in configurable batches
2. **Rate Limiting:** 0.5s delay between API calls
3. **Database Indexes:** Optimized for common query patterns
4. **Connection Pooling:** Uses existing database connection pool
5. **Skip Existing:** Option to skip already-processed locations

## Example Output

```
================================================================================
Starting Bhuvan polygon fetching process
Batch size: 100, Skip existing: True, Dry run: False
================================================================================
Found 1250 unique locations to process
Processing batch 1-100 of 1250
✓ Successfully fetched and stored polygon for village1, district1, state1
✓ Successfully fetched and stored polygon for village2, district2, state2
...
Batch 1-100 completed: Success: 95, Failed: 3, Skipped: 2, Duplicates: 0
...
================================================================================
Polygon fetching process completed
Total processed: 1250
Successful: 1180
Failed: 45
Skipped: 20
Duplicates: 5
================================================================================
```

## Troubleshooting

### Common Issues

1. **"Authentication failed"**
   - Check access token validity
   - Verify token format (Bearer vs query param)

2. **"Village not found"**
   - Verify village/district names match API expectations
   - Check for typos or naming variations

3. **"Rate limit exceeded"**
   - Increase delay between calls (`API_CALL_DELAY`)
   - Process in smaller batches

4. **"Invalid geometry"**
   - Check API response format
   - Verify coordinate ranges

5. **Database errors**
   - Ensure PostGIS extension is enabled
   - Check table exists and permissions
   - Verify foreign key constraints

## API Endpoint Configuration

**Note:** The actual Bhuvan API endpoint may differ. Update the following in `services/bhuvan_polygon_service.py`:

```python
BHUVAN_API_BASE_URL = "https://bhuvan-app1.nrsc.gov.in/api"
BHUVAN_VILLAGE_POLYGON_ENDPOINT = "/village/boundary"  # Verify with API docs
```

Refer to `docs/bhuvan_api_example.md` for example request formats.

## Future Enhancements

- Support for WKT geometry format
- Parallel processing with thread pool
- Progress persistence (resume interrupted runs)
- Webhook notifications on completion
- Polygon simplification for large geometries
- Caching of API responses

## License

[Your License Here]

## Support

For issues or questions, refer to:
- Bhuvan API Documentation: https://bhuvan-app1.nrsc.gov.in/api/
- Logs: `logs/bhuvan_polygon_fetch.log`

