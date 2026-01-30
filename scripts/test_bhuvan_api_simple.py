#!/usr/bin/env python3
"""
Simple test script to verify Bhuvan API connectivity.
Tests multiple endpoint variations and shows responses.
"""

import requests
import json
import sys
import os

# Bhuvan API Configuration
BHUVAN_API_BASE_URL = "https://bhuvan-app1.nrsc.gov.in/api"
ACCESS_TOKEN = "709492afd90de061e1a078ac40647b82b9bf8fa0"

# Endpoint variations to try
ENDPOINT_VARIANTS = [
    "/village/boundary",
    "/village/geocode",
    "/village/polygon",
    "/geocoding/village",
    "/village",
]

# Sample test location
TEST_LOCATION = {
    "village": "delhi",
    "district": "new delhi",
    "state": "delhi"
}

def test_endpoint(endpoint):
    """Test a single endpoint."""
    url = f"{BHUVAN_API_BASE_URL}{endpoint}"
    
    # Try with access_token as query parameter
    params = {
        "village": TEST_LOCATION["village"],
        "district": TEST_LOCATION["district"],
        "state": TEST_LOCATION["state"],
        "access_token": ACCESS_TOKEN
    }
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    print(f"\n{'='*80}")
    print(f"Testing Endpoint: {endpoint}")
    print(f"{'='*80}")
    print(f"URL: {url}")
    print(f"Params: {json.dumps(params, indent=2)}")
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        
        print(f"\nStatus Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                print(f"\n[SUCCESS] Response JSON:")
                print(json.dumps(data, indent=2)[:1000])
                
                # Check for geometry
                geometry = None
                if isinstance(data, dict):
                    if data.get("type") in ["Polygon", "MultiPolygon"]:
                        geometry = data
                    elif data.get("type") == "Feature" and "geometry" in data:
                        geometry = data["geometry"]
                    elif data.get("type") == "FeatureCollection" and "features" in data:
                        if data["features"]:
                            geometry = data["features"][0].get("geometry")
                    elif "geometry" in data:
                        geometry = data["geometry"]
                
                if geometry:
                    geom_type = geometry.get("type")
                    coords = geometry.get("coordinates", [])
                    print(f"\n[GEOMETRY FOUND]")
                    print(f"  Type: {geom_type}")
                    if geom_type == "Polygon" and coords:
                        print(f"  Points: {len(coords[0])}")
                        print(f"  Sample coordinates (first 3):")
                        for i, coord in enumerate(coords[0][:3]):
                            print(f"    [{coord[0]}, {coord[1]}]")
                    return True
                else:
                    print("\n[NO GEOMETRY] Response doesn't contain geometry")
                    return False
                    
            except json.JSONDecodeError:
                print(f"\n[NOT JSON] Response text:")
                print(response.text[:500])
                return False
        else:
            print(f"\n[FAILED] Status: {response.status_code}")
            print(f"Response: {response.text[:500]}")
            
            # Try with Bearer token if 401
            if response.status_code == 401:
                print("\nTrying with Bearer token in header...")
                headers_auth = headers.copy()
                headers_auth["Authorization"] = f"Bearer {ACCESS_TOKEN}"
                params_no_token = {k: v for k, v in params.items() if k != "access_token"}
                
                response2 = requests.get(url, params=params_no_token, headers=headers_auth, timeout=30)
                print(f"Bearer token response: {response2.status_code}")
                if response2.status_code == 200:
                    print("[SUCCESS] Bearer token works!")
                    try:
                        print(json.dumps(response2.json(), indent=2)[:500])
                        return True
                    except:
                        print(response2.text[:500])
            
            return False
            
    except requests.exceptions.Timeout:
        print("\n[TIMEOUT] Request timed out")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"\n[CONNECTION ERROR] {str(e)}")
        return False
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test function."""
    print("="*80)
    print("Bhuvan API Test - Endpoint Discovery")
    print("="*80)
    print(f"Base URL: {BHUVAN_API_BASE_URL}")
    print(f"Test Location: {TEST_LOCATION['village']}, {TEST_LOCATION['district']}, {TEST_LOCATION['state']}")
    print(f"Access Token: {ACCESS_TOKEN[:20]}...")
    
    results = {}
    
    for endpoint in ENDPOINT_VARIANTS:
        success = test_endpoint(endpoint)
        results[endpoint] = success
        
        if success:
            print(f"\n[FOUND WORKING ENDPOINT] {endpoint}")
            break
        
        import time
        time.sleep(1)  # Small delay between requests
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    
    working = [ep for ep, success in results.items() if success]
    failed = [ep for ep, success in results.items() if not success]
    
    if working:
        print(f"[SUCCESS] Working endpoint(s): {', '.join(working)}")
    else:
        print("[FAILED] No working endpoints found")
        print(f"Tested endpoints: {', '.join(ENDPOINT_VARIANTS)}")
        print("\nPossible reasons:")
        print("1. Endpoint path is incorrect")
        print("2. API requires different authentication method")
        print("3. API endpoint structure is different")
        print("4. Village/district names need to match exact API format")
    
    # Save results
    os.makedirs("logs", exist_ok=True)
    with open("logs/bhuvan_api_test_results.json", "w", encoding='utf-8') as f:
        json.dump({
            "test_location": TEST_LOCATION,
            "results": results,
            "working_endpoints": working
        }, f, indent=2)
    
    print(f"\nResults saved to: logs/bhuvan_api_test_results.json")
    print("="*80)
    
    sys.exit(0 if working else 1)

if __name__ == "__main__":
    main()

