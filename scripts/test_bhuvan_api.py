#!/usr/bin/env python3
"""
Test script to verify Bhuvan API connectivity and response format.
Uses sample location data to test the API endpoint.
"""

import requests
import json
import sys
from typing import Dict, Any, Optional

# Bhuvan API Configuration
BHUVAN_API_BASE_URL = "https://bhuvan-app1.nrsc.gov.in/api"
# Try different endpoint variations
ENDPOINT_VARIANTS = [
    "/village/boundary",
    "/village/geocode",
    "/village/polygon",
    "/geocoding/village",
    "/api/village/boundary",
    "/village",
]
ACCESS_TOKEN = "709492afd90de061e1a078ac40647b82b9bf8fa0"

# Sample test data - using common Indian village/district names
SAMPLE_LOCATIONS = [
    {
        "village": "delhi",
        "mandal": None,
        "district": "new delhi",
        "state": "delhi",
        "description": "Test 1: Delhi (capital city)"
    },
    {
        "village": "mumbai",
        "mandal": None,
        "district": "mumbai",
        "state": "maharashtra",
        "description": "Test 2: Mumbai (major city)"
    },
    {
        "village": "bangalore",
        "mandal": None,
        "district": "bangalore urban",
        "state": "karnataka",
        "description": "Test 3: Bangalore (IT hub)"
    },
    {
        "village": "hyderabad",
        "mandal": None,
        "district": "hyderabad",
        "state": "telangana",
        "description": "Test 4: Hyderabad"
    },
    {
        "village": "chennai",
        "mandal": None,
        "district": "chennai",
        "state": "tamil nadu",
        "description": "Test 5: Chennai"
    }
]


def test_api_request(location: Dict[str, Any]) -> Dict[str, Any]:
    """
    Test API request with a single location.
    
    Returns:
        Dictionary with request details and response
    """
    print("\n" + "=" * 80)
    print(f"Testing: {location['description']}")
    print("=" * 80)
    
    # Prepare parameters
    params = {
        "village": location['village'],
        "district": location['district']
    }
    
    if location.get('mandal'):
        params["mandal"] = location['mandal']
    if location.get('state'):
        params["state"] = location['state']
    
    # Try with access token as query parameter
    params["access_token"] = ACCESS_TOKEN
    
    # Prepare headers
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Bhuvan-Polygon-Test/1.0"
    }
    
    # Also try with Bearer token in header
    headers_with_auth = headers.copy()
    headers_with_auth["Authorization"] = f"Bearer {ACCESS_TOKEN}"
    
    result = {
        "location": location,
        "url": None,
        "params": params,
        "success": False,
        "status_code": None,
        "response_text": None,
        "response_json": None,
        "error": None,
        "geometry_found": False,
        "geometry_type": None,
        "coordinate_count": None,
        "endpoint_tested": []
    }
    
    # Try different endpoint variations
    for endpoint in ENDPOINT_VARIANTS:
        url = f"{BHUVAN_API_BASE_URL}{endpoint}"
        result["endpoint_tested"].append(endpoint)
        
        try:
            print(f"\nTrying endpoint: {endpoint}")
            print(f"Request URL: {url}")
            print(f"Parameters: {json.dumps(params, indent=2)}")
            print(f"Headers: {json.dumps(headers, indent=2)}")
            print("\nMaking API request...")
            
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=30
            )
            
            result["url"] = url
        
        result["status_code"] = response.status_code
        result["response_text"] = response.text[:1000]  # First 1000 chars
        
        print(f"\nResponse Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        
        # Try to parse JSON
        try:
            result["response_json"] = response.json()
            print(f"\nResponse JSON (first 500 chars):")
            print(json.dumps(result["response_json"], indent=2)[:500])
        except json.JSONDecodeError:
            print(f"\nResponse is not JSON:")
            print(response.text[:500])
        
        # Check for success
        if response.status_code == 200:
            result["success"] = True
            
            # Try to extract geometry
            data = result["response_json"]
            if data:
                geometry = None
                
                # Check various response formats
                if isinstance(data, dict):
                    if data.get("type") == "Polygon" or data.get("type") == "MultiPolygon":
                        geometry = data
                    elif data.get("type") == "Feature" and "geometry" in data:
                        geometry = data["geometry"]
                    elif data.get("type") == "FeatureCollection" and "features" in data:
                        if data["features"] and len(data["features"]) > 0:
                            geometry = data["features"][0].get("geometry")
                    elif "geometry" in data:
                        geometry = data["geometry"]
                    elif "data" in data and isinstance(data["data"], dict):
                        if "geometry" in data["data"]:
                            geometry = data["data"]["geometry"]
                        elif data["data"].get("type") in ["Polygon", "MultiPolygon"]:
                            geometry = data["data"]
                
                if geometry:
                    result["geometry_found"] = True
                    result["geometry_type"] = geometry.get("type")
                    
                    # Count coordinates
                    coords = geometry.get("coordinates", [])
                    if result["geometry_type"] == "Polygon":
                        if coords and len(coords) > 0:
                            result["coordinate_count"] = len(coords[0])
                    elif result["geometry_type"] == "MultiPolygon":
                        if coords and len(coords) > 0:
                            total_points = sum(len(ring) for polygon in coords for ring in polygon)
                            result["coordinate_count"] = total_points
                    
                    print(f"\n[OK] Geometry Found!")
                    print(f"  Type: {result['geometry_type']}")
                    print(f"  Coordinate Points: {result['coordinate_count']}")
                    
                    # Show sample coordinates
                    if coords:
                        print(f"\n  Sample Coordinates (first 3 points):")
                        if result["geometry_type"] == "Polygon":
                            for i, coord in enumerate(coords[0][:3]):
                                print(f"    Point {i+1}: [{coord[0]}, {coord[1]}]")
                        elif result["geometry_type"] == "MultiPolygon":
                            if coords[0] and coords[0][0]:
                                for i, coord in enumerate(coords[0][0][:3]):
                                    print(f"    Point {i+1}: [{coord[0]}, {coord[1]}]")
                else:
                    print("\n[!] No geometry found in response")
                    print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
        
            # If we got a 200, break out of endpoint loop
            if response.status_code == 200:
                break
            
            # If endpoint doesn't exist (404), try next endpoint
            if response.status_code == 404 and endpoint != ENDPOINT_VARIANTS[-1]:
                print(f"Endpoint {endpoint} returned 404, trying next...")
                continue
            
            # For other errors, try next endpoint unless it's authentication
            if response.status_code == 401:
                result["error"] = "Authentication failed - invalid access token"
                print(f"\n[X] Authentication Error: {result['error']}")
                print("Trying with Bearer token in header...")
                
                # Try with Bearer token
                response2 = requests.get(
                    url,
                    params={k: v for k, v in params.items() if k != "access_token"},
                    headers=headers_with_auth,
                    timeout=30
                )
                
                print(f"Response with Bearer token: {response2.status_code}")
                if response2.status_code == 200:
                    print("[OK] Bearer token authentication works!")
                    response = response2
                    break
            
            elif response.status_code == 404:
                if endpoint == ENDPOINT_VARIANTS[-1]:
                    result["error"] = f"Endpoint not found. Tried: {', '.join(ENDPOINT_VARIANTS)}"
                    print(f"\n[X] All endpoints returned 404")
                continue
            
            elif response.status_code == 429:
                result["error"] = "Rate limit exceeded"
                print(f"\n[X] Rate Limit: {result['error']}")
                break
            
            else:
                if endpoint == ENDPOINT_VARIANTS[-1]:
                    result["error"] = f"HTTP {response.status_code}: {response.text[:200]}"
                    print(f"\n[X] Error: {result['error']}")
                continue
    
    except requests.exceptions.Timeout:
        result["error"] = "Request timeout"
        print(f"\n[X] Timeout: {result['error']}")
    
    except requests.exceptions.ConnectionError as e:
        result["error"] = f"Connection error: {str(e)}"
        print(f"\n[X] Connection Error: {result['error']}")
    
    except Exception as e:
        result["error"] = f"Unexpected error: {str(e)}"
        print(f"\n[X] Error: {result['error']}")
        import traceback
        traceback.print_exc()
    
    return result


def main():
    """Main test function."""
    print("=" * 80)
    print("Bhuvan API Test Script")
    print("=" * 80)
    print(f"API Base URL: {BHUVAN_API_BASE_URL}")
    print(f"Endpoint: {BHUVAN_VILLAGE_POLYGON_ENDPOINT}")
    print(f"Access Token: {ACCESS_TOKEN[:20]}...")
    print("=" * 80)
    
    results = []
    
    # Test each sample location
    for location in SAMPLE_LOCATIONS:
        result = test_api_request(location)
        results.append(result)
        
        # Small delay between requests
        import time
        time.sleep(1)
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    successful = sum(1 for r in results if r["success"])
    geometry_found = sum(1 for r in results if r["geometry_found"])
    
    print(f"Total Tests: {len(results)}")
    print(f"Successful Requests: {successful}")
    print(f"Geometry Found: {geometry_found}")
    print(f"Failed Requests: {len(results) - successful}")
    
    print("\nDetailed Results:")
    for i, result in enumerate(results, 1):
        status = "[OK]" if result["success"] else "[FAIL]"
        geom_status = "[OK]" if result["geometry_found"] else "[NONE]"
        print(f"{i}. {status} {result['location']['description']}")
        print(f"   Status: {result['status_code']}")
        if result["geometry_found"]:
            print(f"   Geometry: {geom_status} {result['geometry_type']} ({result['coordinate_count']} points)")
        if result["error"]:
            print(f"   Error: {result['error']}")
    
    # Save results to file
    output_file = "logs/bhuvan_api_test_results.json"
    try:
        import os
        os.makedirs("logs", exist_ok=True)
        with open(output_file, "w", encoding='utf-8') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n[OK] Results saved to: {output_file}")
    except Exception as e:
        print(f"\n⚠ Could not save results: {e}")
    
    print("=" * 80)
    
    # Exit with error code if all tests failed
    if successful == 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

