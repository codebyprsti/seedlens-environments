"""
Script to validate that all locations from season_crop_inspection_final 
have corresponding IDs in operations.locations table.
"""
import logging
from core.db import SessionLocal, get_connection, release_connection
import psycopg2.extras
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def validate_location_ids():
    """Check if all locations from season_crop_inspection_final exist in locations table."""
    
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    try:
        logger.info("=" * 80)
        logger.info("Starting Location ID Validation")
        logger.info("=" * 80)
        
        # Step 1: Get all unique location combinations from season_crop_inspection_final
        logger.info("Fetching unique locations from season_crop_inspection_final...")
        cur.execute("""
            SELECT DISTINCT
                COALESCE(TRIM(village), '') as village,
                COALESCE(TRIM(taluka_mandal), '') as mandal,
                COALESCE(TRIM(district), '') as district,
                COALESCE(TRIM(state), '') as state,
                COALESCE(TRIM(village_id), '') as village_id
            FROM operations.season_crop_inspection_final
            WHERE village IS NOT NULL 
                AND TRIM(village) != ''
                AND state IS NOT NULL
                AND TRIM(state) != ''
            ORDER BY state, district, mandal, village
        """)
        
        final_locations = cur.fetchall()
        logger.info(f"Found {len(final_locations)} unique location combinations in season_crop_inspection_final")
        
        # Step 2: Get all locations from operations.locations table
        logger.info("Fetching all locations from operations.locations...")
        cur.execute("""
            SELECT 
                location_id,
                COALESCE(TRIM(village), '') as village,
                COALESCE(TRIM(mandal), '') as mandal,
                COALESCE(TRIM(district), '') as district,
                COALESCE(TRIM(state), '') as state,
                mandal_id,
                district_id
            FROM operations.locations
            ORDER BY state, district, mandal, village
        """)
        
        existing_locations = cur.fetchall()
        logger.info(f"Found {len(existing_locations)} locations in operations.locations table")
        
        # State name normalization mapping (same as loader)
        STATE_NORMALIZATION = {
            'chattisgarh': 'Chhattisgarh',
            'chhattisgarh': 'Chhattisgarh',
            'odisha': 'Odisha',
            'andhra pradesh': 'Andhra Pradesh',
            'karnataka': 'Karnataka',
            'telangana': 'Telangana',
            'west bengal': 'West Bengal'
        }
        
        def normalize_state_name(state):
            """Normalize state name to standard format."""
            if not state:
                return state
            state_lower = state.strip().lower()
            return STATE_NORMALIZATION.get(state_lower, state.strip().title())
        
        def normalize_string(value):
            """Normalize string: trim, lowercase for comparison."""
            if not value:
                return ''
            return str(value).strip().lower()
        
        # Step 3: Create lookup dictionaries for faster matching
        # Match by: state, district, mandal, village (case-insensitive, trimmed, normalized)
        location_lookup = {}
        for loc in existing_locations:
            # Normalize state name first
            state_normalized = normalize_state_name(loc['state']) if loc['state'] else ''
            key = (
                normalize_string(state_normalized),
                normalize_string(loc['district']),
                normalize_string(loc['mandal']),
                normalize_string(loc['village'])
            )
            location_lookup[key] = loc
        
        # Step 4: Check each location from final table
        missing_locations = []
        matched_locations = []
        locations_with_mismatched_ids = []
        
        for final_loc in final_locations:
            village = final_loc['village'].strip() if final_loc['village'] else ''
            mandal = final_loc['mandal'].strip() if final_loc['mandal'] else ''
            district = final_loc['district'].strip() if final_loc['district'] else ''
            state_raw = final_loc['state'].strip() if final_loc['state'] else ''
            
            # Skip if essential fields are missing
            if not village or not state_raw:
                continue
            
            # Normalize state name (handles case variations)
            state = normalize_state_name(state_raw)
            
            # Create lookup key with normalized values (same logic as loader)
            lookup_key = (
                normalize_string(state),
                normalize_string(district),
                normalize_string(mandal),
                normalize_string(village)
            )
            
            matched_loc = location_lookup.get(lookup_key)
            
            if matched_loc:
                matched_locations.append({
                    'final': final_loc,
                    'location': matched_loc
                })
                
                # Check if IDs match
                final_village_id = final_loc['village_id'].strip() if final_loc['village_id'] else ''
                location_id = matched_loc['location_id'].strip() if matched_loc['location_id'] else ''
                
                if final_village_id and location_id and final_village_id != location_id:
                    locations_with_mismatched_ids.append({
                        'village': village,
                        'mandal': mandal,
                        'district': district,
                        'state': state,
                        'final_village_id': final_village_id,
                        'location_id': location_id
                    })
            else:
                missing_locations.append({
                    'village': village,
                    'mandal': mandal,
                    'district': district,
                    'state': state,
                    'village_id': final_loc['village_id']
                })
        
        # Step 5: Report results
        logger.info("=" * 80)
        logger.info("VALIDATION RESULTS")
        logger.info("=" * 80)
        logger.info(f"Total locations in season_crop_inspection_final: {len(final_locations)}")
        logger.info(f"Matched locations: {len(matched_locations)}")
        logger.info(f"Missing locations: {len(missing_locations)}")
        logger.info(f"Locations with mismatched IDs: {len(locations_with_mismatched_ids)}")
        logger.info("=" * 80)
        
        # Report missing locations
        if missing_locations:
            logger.warning(f"\n⚠️  MISSING LOCATIONS ({len(missing_locations)}):")
            logger.warning("-" * 80)
            for loc in missing_locations[:50]:  # Show first 50
                logger.warning(
                    f"State: {loc['state']}, District: {loc['district']}, "
                    f"Mandal: {loc['mandal']}, Village: {loc['village']}, "
                    f"Village ID: {loc['village_id']}"
                )
            if len(missing_locations) > 50:
                logger.warning(f"... and {len(missing_locations) - 50} more missing locations")
        
        # Report mismatched IDs
        if locations_with_mismatched_ids:
            logger.warning(f"\n⚠️  LOCATIONS WITH MISMATCHED IDs ({len(locations_with_mismatched_ids)}):")
            logger.warning("-" * 80)
            for loc in locations_with_mismatched_ids[:50]:  # Show first 50
                logger.warning(
                    f"State: {loc['state']}, District: {loc['district']}, "
                    f"Mandal: {loc['mandal']}, Village: {loc['village']}, "
                    f"Final Village ID: {loc['final_village_id']}, "
                    f"Location ID: {loc['location_id']}"
                )
            if len(locations_with_mismatched_ids) > 50:
                logger.warning(f"... and {len(locations_with_mismatched_ids) - 50} more with mismatched IDs")
        
        # Summary by state
        missing_by_state = defaultdict(int)
        for loc in missing_locations:
            missing_by_state[loc['state']] += 1
        
        if missing_by_state:
            logger.info(f"\n📊 MISSING LOCATIONS BY STATE:")
            for state, count in sorted(missing_by_state.items()):
                logger.info(f"  {state}: {count} missing locations")
        
        # Return summary
        return {
            'total_final_locations': len(final_locations),
            'total_existing_locations': len(existing_locations),
            'matched': len(matched_locations),
            'missing': len(missing_locations),
            'mismatched_ids': len(locations_with_mismatched_ids),
            'missing_locations': missing_locations,
            'mismatched_locations': locations_with_mismatched_ids
        }
        
    except Exception as e:
        logger.error(f"Error during validation: {str(e)}", exc_info=True)
        raise
    finally:
        cur.close()
        release_connection(conn)

if __name__ == "__main__":
    try:
        result = validate_location_ids()
        print("\n" + "=" * 80)
        print("VALIDATION COMPLETE")
        print("=" * 80)
        print(f"✅ Matched: {result['matched']}")
        print(f"❌ Missing: {result['missing']}")
        print(f"⚠️  Mismatched IDs: {result['mismatched_ids']}")
        print("=" * 80)
    except Exception as e:
        print(f"\n[ERROR] Error: {str(e)}")
        import traceback
        traceback.print_exc()

