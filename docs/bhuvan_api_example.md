# Bhuvan API Polygon Fetching - Example Request

## Overview
This document provides example API request formats for fetching village-level polygon boundaries from the Bhuvan API.

## API Endpoint
**Base URL:** `https://bhuvan-app1.nrsc.gov.in/api`  
**Endpoint:** `/village/boundary` (assumed - verify with actual API documentation)

## Authentication
The access token can be passed either:
1. **As a query parameter:** `?access_token=YOUR_TOKEN`
2. **As a header:** `Authorization: Bearer YOUR_TOKEN`

## Example Request Formats

### 1. Using Query Parameters (GET Request)

```http
GET https://bhuvan-app1.nrsc.gov.in/api/village/boundary?village=example_village&district=example_district&mandal=example_mandal&state=example_state&access_token=709492afd90de061e1a078ac40647b82b9bf8fa0
```

**cURL Example:**
```bash
curl -X GET "https://bhuvan-app1.nrsc.gov.in/api/village/boundary?village=example_village&district=example_district&mandal=example_mandal&state=example_state&access_token=709492afd90de061e1a078ac40647b82b9bf8fa0" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json"
```

**Python requests Example:**
```python
import requests

url = "https://bhuvan-app1.nrsc.gov.in/api/village/boundary"
params = {
    "village": "example_village",
    "district": "example_district",
    "mandal": "example_mandal",  # Optional
    "state": "example_state",     # Optional
    "access_token": "709492afd90de061e1a078ac40647b82b9bf8fa0"
}

headers = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

response = requests.get(url, params=params, headers=headers)
```

### 2. Using Headers (GET Request)

```http
GET https://bhuvan-app1.nrsc.gov.in/api/village/boundary?village=example_village&district=example_district
Authorization: Bearer 709492afd90de061e1a078ac40647b82b9bf8fa0
Accept: application/json
Content-Type: application/json
```

**Python requests Example:**
```python
import requests

url = "https://bhuvan-app1.nrsc.gov.in/api/village/boundary"
params = {
    "village": "example_village",
    "district": "example_district",
    "mandal": "example_mandal",
    "state": "example_state"
}

headers = {
    "Authorization": "Bearer 709492afd90de061e1a078ac40647b82b9bf8fa0",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

response = requests.get(url, params=params, headers=headers)
```

### 3. Using POST Request (if API supports it)

```http
POST https://bhuvan-app1.nrsc.gov.in/api/village/boundary
Authorization: Bearer 709492afd90de061e1a078ac40647b82b9bf8fa0
Content-Type: application/json

{
  "village": "example_village",
  "district": "example_district",
  "mandal": "example_mandal",
  "state": "example_state"
}
```

**Python requests Example:**
```python
import requests

url = "https://bhuvan-app1.nrsc.gov.in/api/village/boundary"
headers = {
    "Authorization": "Bearer 709492afd90de061e1a078ac40647b82b9bf8fa0",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

payload = {
    "village": "example_village",
    "district": "example_district",
    "mandal": "example_mandal",
    "state": "example_state"
}

response = requests.post(url, json=payload, headers=headers)
```

## Expected Response Formats

### Success Response - GeoJSON Feature
```json
{
  "type": "Feature",
  "geometry": {
    "type": "Polygon",
    "coordinates": [
      [
        [77.123456, 28.654321],
        [77.123789, 28.654321],
        [77.123789, 28.654654],
        [77.123456, 28.654654],
        [77.123456, 28.654321]
      ]
    ]
  },
  "properties": {
    "village": "example_village",
    "district": "example_district",
    "mandal": "example_mandal",
    "state": "example_state"
  }
}
```

### Success Response - GeoJSON FeatureCollection
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [77.123456, 28.654321],
            [77.123789, 28.654321],
            [77.123789, 28.654654],
            [77.123456, 28.654654],
            [77.123456, 28.654321]
          ]
        ]
      },
      "properties": {
        "village": "example_village",
        "district": "example_district"
      }
    }
  ]
}
```

### Success Response - Direct Geometry
```json
{
  "type": "Polygon",
  "coordinates": [
    [
      [77.123456, 28.654321],
      [77.123789, 28.654321],
      [77.123789, 28.654654],
      [77.123456, 28.654654],
      [77.123456, 28.654321]
    ]
  ]
}
```

### Error Response - Not Found
```json
{
  "error": "Village not found",
  "code": 404,
  "message": "No polygon found for the specified village and district"
}
```

### Error Response - Authentication Failed
```json
{
  "error": "Unauthorized",
  "code": 401,
  "message": "Invalid or expired access token"
}
```

### Error Response - Rate Limit Exceeded
```json
{
  "error": "Rate limit exceeded",
  "code": 429,
  "message": "Too many requests. Please try again later.",
  "retry_after": 60
}
```

## Parameter Details

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `village` | string | Yes | Village name (trimmed and lowercased) |
| `district` | string | Yes | District name (trimmed and lowercased) |
| `mandal` | string | No | Mandal/Tehsil name (trimmed and lowercased) |
| `state` | string | No | State name (trimmed and lowercased) |
| `access_token` | string | Yes | API access token |

## Notes

1. **Text Normalization:** All text parameters (village, mandal, district, state) should be:
   - Trimmed (leading/trailing whitespace removed)
   - Lowercased before sending to API

2. **Coordinate Format:** Coordinates are in [longitude, latitude] format (GeoJSON standard)

3. **Polygon Closure:** The first and last coordinate in a polygon ring must be identical (closed ring)

4. **Rate Limiting:** The service includes a 0.5-second delay between API calls to respect rate limits

5. **Retry Logic:** Failed requests are retried up to 2 times with exponential backoff

6. **Error Handling:** The service handles:
   - HTTP errors (401, 404, 429, 500, etc.)
   - Empty responses
   - Invalid JSON
   - Missing geometry in response
   - Invalid geometry coordinates

## Testing

To test the API manually, you can use the following example with real data from your database:

```python
from services.bhuvan_polygon_service import BhuvanPolygonService
from core.db import SessionLocal

# Initialize service
db = SessionLocal()
service = BhuvanPolygonService(db)

# Test with a single location
test_location = {
    'location_id': 'L_12345',
    'village': 'example_village',
    'mandal': 'example_mandal',
    'district': 'example_district',
    'state': 'example_state'
}

result = service._fetch_polygon_from_api(test_location)
print(result)
```

## Important

**⚠️ Note:** The actual Bhuvan API endpoint, request format, and response structure may differ from these examples. Please verify with the official Bhuvan API documentation at:
- https://bhuvan-app1.nrsc.gov.in/api/
- https://bhuvan.nrsc.gov.in/wiki/index.php/Information_for_Developers

Adjust the `BHUVAN_VILLAGE_POLYGON_ENDPOINT` constant in `services/bhuvan_polygon_service.py` based on the actual API specification.

