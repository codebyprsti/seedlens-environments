"""
Script to update village_id in season_crop_inspection_final 
to match location_id from operations.locations table using flexible matching.
"""
import logging
from core.db import get_connection, release_connection
import psycopg2.extras

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

def update_location_ids():
    """Update village_id in season_crop_inspection_final to match location_id from locations table."""
    
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    try:
        logger.info("=" * 80)
        logger.info("Starting Location ID Update Process")
        logger.info("=" * 80)
        
        # Step 1: Get all locations from operations.locations with flexible matching keys
        logger.info("Building location lookup from operations.locations...")
        cur.execute("""
            SELECT 
                location_id,
                COALESCE(TRIM(village), '') as village,
                COALESCE(TRIM(mandal), '') as mandal,
                COALESCE(TRIM(district), '') as district,
                COALESCE(TRIM(state), '') as state
            FROM operations.locations
        """)
        
        existing_locations = cur.fetchall()
        logger.info(f"Found {len(existing_locations)} locations in operations.locations")
        
        # Create flexible lookup
        location_lookup = {}
        for loc in existing_locations:
            state_normalized = normalize_state_name(loc['state']) if loc['state'] else ''
            key = (
                normalize_string(state_normalized),
                normalize_string(loc['district']),
                normalize_string(loc['mandal']),
                normalize_string(loc['village'])
            )
            location_lookup[key] = loc
        
        # Step 2: Get all records from season_crop_inspection_final that need updating
        logger.info("Fetching records from season_crop_inspection_final...")
        # Use composite key: season, crop, hsp_code, grower_id, lot_no, village
        cur.execute("""
            SELECT 
                season,
                crop,
                hsp_code,
                grower_id,
                lot_no,
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
        
        final_records = cur.fetchall()
        logger.info(f"Found {len(final_records)} records in season_crop_inspection_final")
        
        # Step 3: Find records that need ID updates
        updates_needed = []
        for record in final_records:
            village = record['village'].strip() if record['village'] else ''
            mandal = record['mandal'].strip() if record['mandal'] else ''
            district = record['district'].strip() if record['district'] else ''
            original_state = record['state'].strip() if record['state'] else ''
            state = normalize_state_name(original_state) if original_state else ''
            
            if not village or not state:
                continue
            
            lookup_key = (
                normalize_string(state),
                normalize_string(district),
                normalize_string(mandal),
                normalize_string(village)
            )
            
            matched_loc = location_lookup.get(lookup_key)
            if matched_loc:
                current_village_id = record['village_id'].strip() if record['village_id'] else ''
                correct_location_id = matched_loc['location_id'].strip()
                
                # If IDs don't match, add to update list
                if current_village_id != correct_location_id:
                    updates_needed.append({
                        'season': record['season'],
                        'crop': record['crop'],
                        'hsp_code': record['hsp_code'],
                        'grower_id': record['grower_id'],
                        'lot_no': record['lot_no'],
                        'village': village,
                        'current_village_id': current_village_id,
                        'correct_location_id': correct_location_id,
                        'state': state
                    })
        
        logger.info(f"Found {len(updates_needed)} records that need ID updates")
        
        if not updates_needed:
            logger.info("No updates needed - all IDs are correct!")
            return {
                'success': True,
                'updated': 0,
                'message': 'All location IDs are already correct'
            }
        
        # Step 4: Update village_id in batches
        logger.info(f"Updating {len(updates_needed)} records...")
        updated_count = 0
        
        for update in updates_needed:
            try:
                # Use composite key to identify the record
                cur.execute("""
                    UPDATE operations.season_crop_inspection_final
                    SET village_id = %s
                    WHERE season = %s
                        AND crop = %s
                        AND hsp_code = %s
                        AND grower_id = %s
                        AND lot_no = %s
                        AND TRIM(village) = %s
                        AND TRIM(state) = %s
                """, (
                    update['correct_location_id'],
                    update['season'],
                    update['crop'],
                    update['hsp_code'],
                    update['grower_id'],
                    update['lot_no'],
                    update['village'],
                    update['state']
                ))
                updated_count += 1
                
                if updated_count % 100 == 0:
                    conn.commit()
                    logger.info(f"Committed {updated_count} updates...")
                    
            except Exception as e:
                logger.warning(f"Error updating record: {str(e)}")
                continue
        
        # Final commit
        conn.commit()
        logger.info(f"Successfully updated {updated_count} records")
        
        logger.info("=" * 80)
        logger.info("UPDATE COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Updated: {updated_count} records")
        logger.info("=" * 80)
        
        return {
            'success': True,
            'updated': updated_count,
            'total_needing_update': len(updates_needed),
            'message': f'Successfully updated {updated_count} location IDs'
        }
        
    except Exception as e:
        logger.error(f"Error during update process: {str(e)}", exc_info=True)
        conn.rollback()
        raise
    finally:
        cur.close()
        release_connection(conn)

if __name__ == "__main__":
    try:
        result = update_location_ids()
        print("\n" + "=" * 80)
        print("UPDATE COMPLETE")
        print("=" * 80)
        print(f"[SUCCESS] Updated: {result['updated']} records")
        if 'total_needing_update' in result:
            print(f"[INFO] Total needing update: {result['total_needing_update']} records")
        print("=" * 80)
    except Exception as e:
        print(f"\n[ERROR] Error: {str(e)}")
        import traceback
        traceback.print_exc()

