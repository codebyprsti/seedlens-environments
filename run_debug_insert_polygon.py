"""One-off script to run debug_force_insert and print endpoint-style output."""
import sys
from core.db import SessionLocal
from services.bhuvan_polygon_service import BhuvanPolygonService

def main():
    db = SessionLocal()
    try:
        service = BhuvanPolygonService(db)
        result = service.debug_force_insert()
        print(result)
        return 0
    except Exception as e:
        print({"detail": str(e)}, file=sys.stderr)
        return 1
    finally:
        db.close()

if __name__ == "__main__":
    sys.exit(main())
