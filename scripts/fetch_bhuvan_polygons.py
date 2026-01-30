#!/usr/bin/env python3
"""
Script to fetch village polygons from Bhuvan API and store in database.

Usage:
    python scripts/fetch_bhuvan_polygons.py [options]

Options:
    --batch-size N       Process N records per batch (default: 100)
    --skip-existing      Skip locations that already have polygons (default: True)
    --no-skip-existing   Process all locations, including existing ones
    --dry-run            Show what would be done without making API calls
    --access-token TOKEN Use custom access token (default: from service)
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session
from core.db import SessionLocal
from services.bhuvan_polygon_service import BhuvanPolygonService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bhuvan_polygon_fetch.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


def main():
    """Main function to run the polygon fetching process."""
    parser = argparse.ArgumentParser(
        description='Fetch village polygons from Bhuvan API'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=100,
        help='Number of records to process per batch (default: 100)'
    )
    parser.add_argument(
        '--skip-existing',
        action='store_true',
        default=True,
        help='Skip locations that already have polygons (default: True)'
    )
    parser.add_argument(
        '--no-skip-existing',
        dest='skip_existing',
        action='store_false',
        help='Process all locations, including existing ones'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making API calls'
    )
    parser.add_argument(
        '--access-token',
        type=str,
        default=None,
        help='Custom access token (default: from service)'
    )
    
    args = parser.parse_args()
    
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    logger.info("=" * 80)
    logger.info("Bhuvan Polygon Fetching Script")
    logger.info("=" * 80)
    logger.info(f"Batch size: {args.batch_size}")
    logger.info(f"Skip existing: {args.skip_existing}")
    logger.info(f"Dry run: {args.dry_run}")
    logger.info("=" * 80)
    
    db: Session = None
    service: BhuvanPolygonService = None
    
    try:
        # Initialize database session
        db = SessionLocal()
        
        # Initialize service
        if args.access_token:
            service = BhuvanPolygonService(db, access_token=args.access_token)
        else:
            service = BhuvanPolygonService(db)
        
        # Fetch polygons
        stats = service.fetch_all_polygons(
            batch_size=args.batch_size,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run
        )
        
        # Print summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Total processed: {stats['total_processed']}")
        print(f"Successful: {stats['successful']}")
        print(f"Failed: {stats['failed']}")
        print(f"Skipped: {stats['skipped']}")
        print(f"Duplicates: {stats['duplicates']}")
        
        if stats['errors']:
            print(f"\nErrors ({len(stats['errors'])}):")
            for error in stats['errors'][:10]:  # Show first 10 errors
                print(f"  - {error.get('location_id')}: {error.get('error')}")
            if len(stats['errors']) > 10:
                print(f"  ... and {len(stats['errors']) - 10} more errors")
        
        print("=" * 80)
        
        # Exit with error code if there were failures
        if stats['failed'] > 0:
            sys.exit(1)
        else:
            sys.exit(0)
            
    except KeyboardInterrupt:
        logger.warning("Process interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Critical error: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        if service:
            service.close()
        if db:
            db.close()


if __name__ == "__main__":
    main()

