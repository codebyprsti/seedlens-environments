"""
Diagnostic script to check planning data and matching
"""
from core.db import get_db
from sqlalchemy import func
from models.db_models import SupplyChainPlanning, YieldInspectionView

def _normalize_key(value):
    if value is None:
        return ""
    return str(value).strip().lower()

db = next(get_db())

print("="*80)
print("PLANNING DATA DIAGNOSTICS")
print("="*80)

# 1. Check total planning records
total_planning = db.query(func.count(SupplyChainPlanning.id)).scalar() or 0
print(f"\n1. Total planning records in database: {total_planning:,}")

if total_planning == 0:
    print("   ⚠️  NO PLANNING DATA FOUND - This is why all planning fields are 0!")
    print("   You need to load planning data into operations.supply_chain_planning table.")
    exit(0)

# 2. Show sample planning records
print("\n2. Sample planning records (first 10):")
sample = db.query(
    SupplyChainPlanning.season,
    SupplyChainPlanning.crop,
    SupplyChainPlanning.variety,
    SupplyChainPlanning.village,
    SupplyChainPlanning.plan_revision_version,
    func.sum(SupplyChainPlanning.net_acres_current).label("net_acres"),
    func.sum(SupplyChainPlanning.production_allocation).label("prod_alloc")
).group_by(
    SupplyChainPlanning.season,
    SupplyChainPlanning.crop,
    SupplyChainPlanning.variety,
    SupplyChainPlanning.village,
    SupplyChainPlanning.plan_revision_version
).limit(10).all()

for i, p in enumerate(sample, 1):
    print(f"   {i}. Season: '{p.season}' | Crop: '{p.crop}' | Variety: '{p.variety}' | Village: '{p.village or '(NULL)'}'")
    print(f"      Normalized: season='{_normalize_key(p.season)}', crop='{_normalize_key(p.crop)}', variety='{_normalize_key(p.variety)}', village='{_normalize_key(p.village)}'")
    print(f"      Net Acres: {p.net_acres}, Prod Allocation: {p.prod_alloc}")

# 3. Check for RABI 24-25 and Rice specifically
print("\n3. Planning records matching 'RABI 24-25' and 'Rice':")
rabi_rice = db.query(
    SupplyChainPlanning.season,
    SupplyChainPlanning.crop,
    SupplyChainPlanning.variety,
    func.count(SupplyChainPlanning.id).label("count")
).filter(
    func.lower(func.trim(SupplyChainPlanning.season)).ilike("%rabi%"),
    func.lower(func.trim(SupplyChainPlanning.crop)).ilike("%rice%")
).group_by(
    SupplyChainPlanning.season,
    SupplyChainPlanning.crop,
    SupplyChainPlanning.variety
).limit(10).all()

if rabi_rice:
    print(f"   Found {len(rabi_rice)} unique combinations:")
    for r in rabi_rice:
        print(f"   - Season: '{r.season}' | Crop: '{r.crop}' | Variety: '{r.variety}' | Count: {r.count}")
        print(f"     Normalized: season='{_normalize_key(r.season)}', crop='{_normalize_key(r.crop)}', variety='{_normalize_key(r.variety)}'")
else:
    print("   ⚠️  NO MATCHING RECORDS FOUND for 'RABI' + 'Rice'")

# 4. Check yield data values
print("\n4. Sample yield data values (from your query):")
yield_sample = db.query(
    YieldInspectionView.season_name,
    YieldInspectionView.crop_name,
    YieldInspectionView.variety_name,
    YieldInspectionView.village
).filter(
    YieldInspectionView.season_name.ilike("%RABI 24-25%"),
    YieldInspectionView.crop_name.ilike("%Rice%")
).distinct().limit(5).all()

if yield_sample:
    print(f"   Found {len(yield_sample)} unique combinations:")
    for y in yield_sample:
        print(f"   - Season: '{y.season_name}' | Crop: '{y.crop_name}' | Variety: '{y.variety_name}' | Village: '{y.village or '(NULL)'}'")
        print(f"     Normalized: season='{_normalize_key(y.season_name)}', crop='{_normalize_key(y.crop_name)}', variety='{_normalize_key(y.variety_name)}', village='{_normalize_key(y.village)}'")
else:
    print("   ⚠️  NO YIELD DATA FOUND")

# 5. Try to match
print("\n5. Matching test:")
if rabi_rice and yield_sample:
    print("   Comparing first planning record with first yield record:")
    p = rabi_rice[0]
    y = yield_sample[0]
    
    p_key = (_normalize_key(p.season), _normalize_key(p.crop), _normalize_key(p.variety))
    y_key = (_normalize_key(y.season_name), _normalize_key(y.crop_name), _normalize_key(y.variety_name))
    
    print(f"   Planning key: {p_key}")
    print(f"   Yield key:    {y_key}")
    print(f"   Match: {'✅ YES' if p_key == y_key else '❌ NO'}")

print("\n" + "="*80)
print("DIAGNOSIS COMPLETE")
print("="*80)

db.close()
