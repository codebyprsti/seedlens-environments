# Logging Structure for Bhuvan Polygon Fetching

## Log Levels

### INFO
- Process start/completion
- Batch progress
- Successful polygon fetches
- Statistics summaries

### WARNING
- Skipped records (null village/district)
- API retries
- Rate limit warnings
- Duplicate polygon detection
- Partial API responses

### ERROR
- Failed API requests
- Authentication failures
- Invalid geometry
- Database errors
- Parse errors

### DEBUG
- Detailed API request/response data
- Geometry validation details
- Database query details

## Log Format

```
TIMESTAMP - LOGGER_NAME - LEVEL - MESSAGE
```

Example:
```
2024-01-15 10:30:45,123 - services.bhuvan_polygon_service - INFO - Starting Bhuvan polygon fetching process
2024-01-15 10:30:45,456 - services.bhuvan_polygon_service - INFO - Found 1250 unique locations to process
2024-01-15 10:30:46,789 - services.bhuvan_polygon_service - WARNING - Skipping location L_12345: village or district is null
2024-01-15 10:30:47,012 - services.bhuvan_polygon_service - ERROR - Failed to fetch polygon for location_id L_67890: Village not found
```

## Log File Location

- **File:** `logs/bhuvan_polygon_fetch.log`
- **Rotation:** Manual (consider using logging.handlers.RotatingFileHandler for production)

## Log Categories

### 1. Process Lifecycle
```
INFO - Starting Bhuvan polygon fetching process
INFO - Batch size: 100, Skip existing: True, Dry run: False
INFO - Found N unique locations to process
INFO - Processing batch X-Y of N
INFO - Polygon fetching process completed
```

### 2. Success Logging
```
INFO - ✓ Successfully fetched and stored polygon for {village}, {district}, {state}
DEBUG - Stored polygon for location_id: {location_id}
```

### 3. Error Logging
```
ERROR - Failed to fetch polygon for location_id {location_id}: {error}
ERROR - Error storing polygon for location_id {location_id}: {error}
ERROR - Critical error in fetch_all_polygons: {error}
```

### 4. Warning Logging
```
WARNING - Skipping location {location_id}: village or district is null
WARNING - Rate limit exceeded, waiting {wait_time}s before retry
WARNING - Polygon already exists for location_id: {location_id}
WARNING - API error: HTTP {status_code}: {response_text}
```

### 5. API Request Logging
```
DEBUG - API request attempt {attempt}/{max_retries} for {village}, {district}
DEBUG - API response: {response_status} - {response_preview}
```

### 6. Statistics Logging
```
INFO - Batch X-Y completed: Success: N, Failed: M, Skipped: K, Duplicates: L
INFO - Total processed: N
INFO - Successful: M
INFO - Failed: K
INFO - Skipped: L
INFO - Duplicates: D
```

## Error Details Structure

Errors logged include:
- **Location ID:** For tracking which location failed
- **Village/District:** Administrative details
- **Error Message:** Specific error description
- **Stack Trace:** For unexpected errors (exc_info=True)

Example error entry:
```python
{
    "location_id": "L_12345",
    "village": "example_village",
    "district": "example_district",
    "error": "Village not found: example_village, example_district"
}
```

## Logging Configuration

Configured in `scripts/fetch_bhuvan_polygons.py`:

```python
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bhuvan_polygon_fetch.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
```

## Production Recommendations

1. **Use RotatingFileHandler:**
```python
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    'logs/bhuvan_polygon_fetch.log',
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)
```

2. **Add Structured Logging:**
Consider using `structlog` or `python-json-logger` for structured JSON logs.

3. **Separate Log Files:**
- `bhuvan_polygon_fetch.log` - General process logs
- `bhuvan_polygon_errors.log` - Error-only logs
- `bhuvan_polygon_api.log` - API request/response logs (DEBUG level)

4. **Log Aggregation:**
For production, consider integrating with:
- ELK Stack (Elasticsearch, Logstash, Kibana)
- Splunk
- CloudWatch (AWS)
- Application Insights (Azure)

## Querying Logs

### Find all errors:
```bash
grep "ERROR" logs/bhuvan_polygon_fetch.log
```

### Find failed locations:
```bash
grep "Failed to fetch polygon" logs/bhuvan_polygon_fetch.log
```

### Find rate limit issues:
```bash
grep "Rate limit exceeded" logs/bhuvan_polygon_fetch.log
```

### Count successes:
```bash
grep "Successfully fetched and stored polygon" logs/bhuvan_polygon_fetch.log | wc -l
```

