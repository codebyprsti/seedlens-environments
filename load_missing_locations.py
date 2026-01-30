"""
Script to load missing locations from season_crop_inspection_final 
into operations.locations table with flexible matching (case-insensitive, trimmed).
"""
import logging
from core.db import SessionLocal, get_connection, release_connection
from models.db_models import LocationRecord
from models.schemas.base import LocationRecordCreate
import psycopg2.extras
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# State name normalization mapping
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

def get_next_location_id(conn):
    """Get the next available location_id following the pattern L_XXXXXX."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT location_id 
            FROM operations.locations 
            WHERE location_id ~ '^L_\\d+$'
            ORDER BY CAST(SUBSTRING(location_id FROM 'L_(\\d+)') AS INTEGER) DESC 
            LIMIT 1
        """)
        row = cur.fetchone()
        cur.close()
        
        if row:
            # Extract number from L_XXXXXX format
            match = re.search(r'L_(\d+)', row[0])
            if match:
                next_num = int(match.group(1)) + 1
            else:
                next_num = 503428  # Start from current max + 1
        else:
            next_num = 503428  # Start from current max + 1
        
        return f"L_{next_num:06d}"
    except Exception as e:
        logger.warning(f"Error getting next location_id, using default: {e}")
        return "L_503428"

def load_missing_locations():
    """Load missing locations from season_crop_inspection_final into locations table."""
    
    db = SessionLocal()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    try:
        logger.info("=" * 80)
        logger.info("Starting Missing Locations Load Process")
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
        """)
        
        final_locations = cur.fetchall()
        logger.info(f"Found {len(final_locations)} unique location combinations")
        
        # Step 2: Get all existing locations with flexible matching
        logger.info("Fetching existing locations from operations.locations...")
        cur.execute("""
            SELECT 
                location_id,
                village,
                mandal,
                district,
                state
            FROM operations.locations
        """)
        
        existing_locations = cur.fetchall()
        logger.info(f"Found {len(existing_locations)} existing locations")
        
        # Step 3: Create flexible lookup (case-insensitive, trimmed, space-sensitive)
        # Match by: state, district, mandal, village (all normalized to lowercase, trimmed)
        location_lookup = {}
        for loc in existing_locations:
            # Normalize state name first, then lowercase
            state_normalized = normalize_state_name(loc['state']) if loc['state'] else ''
            key = (
                normalize_string(state_normalized),
                normalize_string(loc['district']),
                normalize_string(loc['mandal']),
                normalize_string(loc['village'])
            )
            location_lookup[key] = loc
        
        # Step 4: Find missing locations with flexible matching
        missing_locations = []
        for final_loc in final_locations:
            # Get raw values and normalize
            village_raw = final_loc['village'].strip() if final_loc['village'] else ''
            mandal_raw = final_loc['mandal'].strip() if final_loc['mandal'] else ''
            district_raw = final_loc['district'].strip() if final_loc['district'] else ''
            state_raw = final_loc['state'].strip() if final_loc['state'] else ''
            
            # Skip if essential fields are missing
            if not village_raw or not state_raw:
                continue
            
            # Normalize state name (handles case variations like "Chattisgarh" vs "Chhattisgarh")
            state_normalized = normalize_state_name(state_raw)
            
            # Create lookup key with normalized values (lowercase, trimmed)
            lookup_key = (
                normalize_string(state_normalized),
                normalize_string(district_raw),
                normalize_string(mandal_raw),
                normalize_string(village_raw)
            )
            
            # Check if location exists (flexible matching - case-insensitive, trimmed)
            if lookup_key not in location_lookup:
                missing_locations.append({
                    'village': village_raw,  # Keep original for now, will be lowercased when inserting
                    'mandal': mandal_raw if mandal_raw else None,
                    'district': district_raw if district_raw else None,
                    'state': state_normalized,
                    'source_location_id': final_loc['village_id'].strip() if final_loc['village_id'] else None
                })
        
        logger.info(f"Found {len(missing_locations)} missing locations to load")
        
        if not missing_locations:
            logger.info("No missing locations to load!")
            return {
                'success': True,
                'loaded': 0,
                'skipped': 0,
                'total_missing': 0,
                'message': 'All locations already exist'
            }
        
        # Step 5: Generate location_ids and prepare records
        logger.info("Generating location_ids and preparing records...")
        location_records = []
        
        # Get starting location_id
        cur2 = conn.cursor()
        try:
            cur2.execute("""
                SELECT location_id 
                FROM operations.locations 
                WHERE location_id ~ '^L_\\d+$'
                ORDER BY CAST(SUBSTRING(location_id FROM 'L_(\\d+)') AS INTEGER) DESC 
                LIMIT 1
            """)
            row = cur2.fetchone()
            if row:
                match = re.search(r'L_(\d+)', row[0])
                if match:
                    current_id_num = int(match.group(1)) + 1
                else:
                    current_id_num = 503428
            else:
                current_id_num = 503428
        except Exception as e:
            logger.warning(f"Could not determine starting location_id: {e}")
            current_id_num = 503428
        finally:
            cur2.close()
        
        for loc in missing_locations:
            location_id = f"L_{current_id_num:06d}"
            current_id_num += 1
            
            # Store villages in lowercase, trim all fields, normalize spaces
            location_records.append({
                'location_id': location_id,
                'source_location_id': loc['source_location_id'],
                'village': normalize_string(loc['village']),  # Store in lowercase
                'mandal': normalize_string(loc['mandal']) if loc['mandal'] else None,
                'district': normalize_string(loc['district']) if loc['district'] else None,
                'state': normalize_state_name(loc['state']),  # Normalize state name
                'category_id': 100004  # Default category for locations
            })
        
        # Step 6: Insert missing locations
        logger.info(f"Inserting {len(location_records)} new location records...")
        inserted_count = 0
        skipped_count = 0
        
        for record in location_records:
            try:
                # Check if location already exists (flexible match) before inserting
                lookup_key = (
                    normalize_string(record['state']),
                    normalize_string(record['district'] or ''),
                    normalize_string(record['mandal'] or ''),
                    normalize_string(record['village'])
                )
                
                if lookup_key in location_lookup:
                    skipped_count += 1
                    continue
                
                # Create location record
                location_schema = LocationRecordCreate(**record)
                location_obj = LocationRecord(**location_schema.model_dump())
                db.add(location_obj)
                
                # Update lookup to avoid duplicates in same batch
                location_lookup[lookup_key] = {'location_id': record['location_id']}
                inserted_count += 1
                
            except Exception as e:
                logger.warning(f"Error inserting location {record}: {str(e)}")
                skipped_count += 1
                continue
        
        # Commit all inserts
        if inserted_count > 0:
            db.commit()
            logger.info(f"Successfully inserted {inserted_count} new location records")
        
        logger.info("=" * 80)
        logger.info("LOAD COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Inserted: {inserted_count}")
        logger.info(f"Skipped: {skipped_count}")
        logger.info("=" * 80)
        
        return {
            'success': True,
            'loaded': inserted_count,
            'skipped': skipped_count,
            'total_missing': len(missing_locations),
            'message': f'Successfully loaded {inserted_count} missing locations'
        }
        
    except Exception as e:
        logger.error(f"Error during load process: {str(e)}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
        cur.close()
        release_connection(conn)

if __name__ == "__main__":
    try:
        result = load_missing_locations()
        print("\n" + "=" * 80)
        print("LOAD COMPLETE")
        print("=" * 80)
        print(f"[SUCCESS] Loaded: {result['loaded']} locations")
        print(f"[INFO] Skipped: {result['skipped']} locations")
        print(f"[INFO] Total missing: {result['total_missing']} locations")
        print("=" * 80)
    except Exception as e:
        print(f"\n[ERROR] Error: {str(e)}")
        import traceback
        traceback.print_exc()

