"""Test script to verify dropdown states and yield summary totals"""
import json
import urllib.request
import urllib.parse
import sys

# Fix Windows console encoding
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

BASE_URL = "http://127.0.0.1:8008/api/v1"

def test_dropdown_states():
    """Test dropdown states endpoint"""
    print("=" * 60)
    print("TEST 1: Dropdown States (variety_id -> states)")
    print("=" * 60)
    
    params = {
        "season_id": "RABI_24_25",
        "crop_id": "CR_001",
        "variety_id": "VR_1001"
    }
    url = f"{BASE_URL}/dropdown-options?" + urllib.parse.urlencode(params)
    
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read())
            print(f"Response: {json.dumps(data, indent=2)}")
            if "state" in data:
                print(f"\n[SUCCESS] Found {len(data['state'])} states")
                print(f"  States: {data['state'][:5]}...")  # Show first 5
            else:
                print("\n[FAILED] 'state' key not found in response")
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()

def test_dropdown_villages():
    """Test dropdown villages endpoint"""
    print("\n" + "=" * 60)
    print("TEST 2: Dropdown Villages (variety_id + state -> villages)")
    print("=" * 60)
    
    params = {
        "season_id": "RABI_24_25",
        "crop_id": "CR_001",
        "variety_id": "VR_1001",
        "state": "Telangana"
    }
    url = f"{BASE_URL}/dropdown-options?" + urllib.parse.urlencode(params)
    
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read())
            print(f"Response: {json.dumps(data, indent=2)}")
            if "village" in data:
                print(f"\n[SUCCESS] Found {len(data['village'])} villages")
                print(f"  Villages: {data['village'][:5]}...")  # Show first 5
            else:
                print("\n[FAILED] 'village' key not found in response")
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()

def test_yield_summary_with_state():
    """Test yield summary with state filter"""
    print("\n" + "=" * 60)
    print("TEST 3: Yield Summary with State Filter")
    print("=" * 60)
    
    params = {
        "season_id": "RABI_24_25",
        "crop_id": "CR_001",
        "variety_id": "VR_1001",
        "state": "Telangana",
        "limit": 10
    }
    url = f"{BASE_URL}/yield-summary?" + urllib.parse.urlencode(params)
    
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read())
            print(f"Response: {len(data)} rows returned")
            
            if data:
                print(f"\nFirst row sample:")
                first_row = data[0]
                print(f"  Season: {first_row.get('season_name')}")
                print(f"  Crop: {first_row.get('crop_name')}")
                print(f"  Variety: {first_row.get('variety_name')}")
                print(f"  State: {first_row.get('state')}")
                print(f"  Village: {first_row.get('village')}")
                print(f"  Total Received Qty: {first_row.get('total_received_qty')}")
                print(f"  Total Packed Qty: {first_row.get('total_packed_qty')}")
                print(f"  Avg Productivity: {first_row.get('avg_productivity')}")
                
                # Check if states are populated
                states_found = set(row.get('state') for row in data if row.get('state'))
                print(f"\n[SUCCESS] States found in results: {sorted(states_found)}")
                
                # Calculate totals
                total_received = sum(row.get('total_received_qty', 0) or 0 for row in data)
                total_packed = sum(row.get('total_packed_qty', 0) or 0 for row in data)
                print(f"\n[INFO] Aggregated Totals:")
                print(f"  Sum of Total Received Qty: {total_received}")
                print(f"  Sum of Total Packed Qty: {total_packed}")
            else:
                print("\n✗ FAILED: No data returned")
    except Exception as e:
        print(f"\n✗ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()

def test_yield_summary_totals():
    """Test yield summary totals aggregation"""
    print("\n" + "=" * 60)
    print("TEST 4: Yield Summary Totals Verification")
    print("=" * 60)
    
    params = {
        "season_id": "RABI_24_25",
        "crop_id": "CR_001",
        "variety_id": "VR_1001",
        "limit": 100
    }
    url = f"{BASE_URL}/yield-summary?" + urllib.parse.urlencode(params)
    
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read())
            print(f"Response: {len(data)} rows returned")
            
            if data:
                # Calculate totals
                total_received = sum(row.get('total_received_qty', 0) or 0 for row in data)
                total_packed = sum(row.get('total_packed_qty', 0) or 0 for row in data)
                total_net_acres = sum(row.get('total_net_acres', 0) or 0 for row in data if 'total_net_acres' in row)
                
                print(f"\n[INFO] Aggregated Totals:")
                print(f"  Sum of Total Received Qty: {total_received:,.2f}")
                print(f"  Sum of Total Packed Qty: {total_packed:,.2f}")
                
                # Check for duplicate states
                states = [row.get('state') for row in data if row.get('state')]
                unique_states = set(states)
                print(f"\n[INFO] State Distribution:")
                print(f"  Total rows with state: {len(states)}")
                print(f"  Unique states: {sorted(unique_states)}")
                
                # Sample rows
                print(f"\n[INFO] Sample rows (first 3):")
                for i, row in enumerate(data[:3]):
                    print(f"  Row {i+1}: state={row.get('state')}, received={row.get('total_received_qty')}, packed={row.get('total_packed_qty')}")
            else:
                print("\n✗ FAILED: No data returned")
    except Exception as e:
        print(f"\n✗ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("TESTING DROPDOWN AND YIELD SUMMARY ENDPOINTS")
    print("=" * 60)
    
    test_dropdown_states()
    test_dropdown_villages()
    test_yield_summary_with_state()
    test_yield_summary_totals()
    
    print("\n" + "=" * 60)
    print("TESTING COMPLETE")
    print("=" * 60)
