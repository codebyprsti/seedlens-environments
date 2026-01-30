"""
Alternative script to load planning data - standalone version without record_service.py
"""
import sys
import os
import logging
import time
import pandas as pd
import re
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from core.db import SessionLocal, get_connection, release_connection
from models.db_models import (
    SupplyChainPlanning, CropRecord, VarietyRecord, LocationRecord, SeasonRecord
)
import psycopg2
import psycopg2.extras

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


class PlanningDataLoader:
    """Standalone planning data loader without record_service dependency"""
    
    def __init__(self, db: Session):
        self.db = db
        self.logger = logger
        self.processed_counts = {
            'rows_processed': 0,
            'rows_inserted': 0,
            'rows_skipped': 0,
            'master_entities_created': 0
        }
    
    def load_excel(self, file_path: str) -> pd.DataFrame:
        """Load Excel file into DataFrame"""
        try:
            self.logger.info(f"Loading Excel file: {file_path}")
            df = pd.read_excel(file_path, engine='openpyxl')
            df = df.dropna(how='all')
            df = df.where(pd.notnull(df), None)
            self.logger.info(f"Loaded {len(df)} rows and {len(df.columns)} columns")
            return df
        except Exception as e:
            self.logger.error(f"Error loading Excel: {str(e)}")
            raise
    
    def normalize_column_name(self, col_name: str) -> str:
        """Normalize column names"""
        if pd.isna(col_name) or col_name is None:
            return None
        normalized = str(col_name).strip().lower()
        normalized = re.sub(r'[\s\-]+', '_', normalized)
        normalized = re.sub(r'[^\w_]', '', normalized)
        normalized = re.sub(r'_+', '_', normalized).strip('_')
        return normalized
    
    def normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize all column names with proper mapping"""
        column_mapping = {}
        
        self.logger.info(f"Original columns: {list(df.columns)}")
        
        for col in df.columns:
            original_col = col
            if not isinstance(original_col, str):
                column_mapping[original_col] = str(original_col)
                continue
            
            original_lower = original_col.lower().strip()
            
            # Special handling: Map "Male Soaking Acre" to "net_acres_current"
            if 'male' in original_lower and 'soaking' in original_lower and 'acre' in original_lower:
                column_mapping[original_col] = 'net_acres_current'
                self.logger.info(f"Mapped column '{original_col}' -> 'net_acres_current' (Male Soaking Acre)")
                continue
            
            # Special handling: Map "Targeted Yield" or "Target Yield" to "production_allocation"
            if ('targeted' in original_lower and 'yield' in original_lower) or \
               ('target' in original_lower and 'yield' in original_lower):
                if original_lower != 'location':  # Don't match "Location"
                    column_mapping[original_col] = 'production_allocation'
                    self.logger.info(f"Mapped column '{original_col}' -> 'production_allocation' (Targeted Yield)")
                    continue
            
            # Special handling: Map "Taluka/Mandal" to "mandal"
            if 'taluka' in original_lower and 'mandal' in original_lower:
                column_mapping[original_col] = 'mandal'
                self.logger.info(f"Mapped column '{original_col}' -> 'mandal' (Taluka/Mandal)")
                continue
            
            # Special handling: Map "Grower Name" to "grower"
            if 'grower' in original_lower and 'name' in original_lower:
                column_mapping[original_col] = 'grower'
                self.logger.info(f"Mapped column '{original_col}' -> 'grower' (Grower Name)")
                continue
            
            # Special handling: Map "HSP Code" to "variety"
            if ('hsp' in original_lower and 'code' in original_lower) or original_lower == 'hsp_code':
                column_mapping[original_col] = 'variety'
                self.logger.info(f"Mapped column '{original_col}' -> 'variety' (HSP Code)")
                continue
            
            # Special handling: Map "Location" to "location" (not production_allocation)
            if original_lower == 'location':
                column_mapping[original_col] = 'location'
                self.logger.info(f"Mapped column '{original_col}' -> 'location'")
                continue
            
            # Special handling: Map "Productivity" to "productivity"
            if original_lower == 'productivity' or 'productivity' in original_lower:
                column_mapping[original_col] = 'productivity'
                self.logger.info(f"Mapped column '{original_col}' -> 'productivity'")
                continue
            
            # Special handling: Exclude "S.No" or "S.No" variations
            if original_lower in ['s.no', 's_no', 'sno', 'serial_no', 'serial_number']:
                column_mapping[original_col] = 'sno'  # Will be excluded later
                self.logger.info(f"Excluding column '{original_col}' (S.No)")
                continue
            
            # Normalize the column name
            normalized = self.normalize_column_name(col)
            if normalized:
                column_mapping[col] = normalized
            else:
                column_mapping[col] = col
        
        # Apply additional special mappings for normalized names
        special_mappings = {
            'hybrid': 'variety',
            'allotted_target': 'production_allocation',
            'estimated_net_acres_soaked_used': 'net_acres_current',
            'estimated_net_acres_soakedused': 'net_acres_current',
            'male_soaking_acre': 'net_acres_current',  # Map normalized "Male Soaking Acre"
            'targeted_yield': 'production_allocation',  # Map normalized "Targeted Yield"
            'target_yield': 'production_allocation'  # Map normalized "Target Yield"
        }
        
        for old_col, new_col in special_mappings.items():
            if old_col in column_mapping.values():
                column_mapping = {k: (new_col if v == old_col else v) for k, v in column_mapping.items()}
                self.logger.info(f"Applied special mapping: '{old_col}' -> '{new_col}'")
        
        df = df.rename(columns=column_mapping)
        
        # Remove excluded columns (sno, etc.)
        columns_to_drop = [col for col in df.columns if col in ['sno', 's_no', 's.no']]
        if columns_to_drop:
            df = df.drop(columns=columns_to_drop)
            self.logger.info(f"Dropped excluded columns: {columns_to_drop}")
        
        self.logger.info(f"Final columns after normalization: {list(df.columns)}")
        return df
    
    def get_or_create_season(self, season_name: str) -> str:
        """Get or create season and return season_id"""
        if not season_name or pd.isna(season_name):
            return None
        
        season_name = str(season_name).strip()
        if not season_name:
            return None
        
        season_id = season_name.replace(' ', '_').replace('-', '_').upper()
        
        existing = self.db.query(SeasonRecord).filter(
            SeasonRecord.season_id == season_id
        ).first()
        
        if existing:
            return season_id
        
        try:
            new_season = SeasonRecord(season_id=season_id, season_name=season_name)
            self.db.add(new_season)
            self.db.commit()
            self.processed_counts['master_entities_created'] += 1
            self.logger.info(f"Created season: {season_id}")
            return season_id
        except Exception as e:
            self.db.rollback()
            existing = self.db.query(SeasonRecord).filter(
                SeasonRecord.season_id == season_id
            ).first()
            if existing:
                return season_id
            raise
    
    def get_or_create_crop(self, crop_name: str) -> str:
        """Get or create crop and return crop_id"""
        if not crop_name or pd.isna(crop_name):
            return None
        
        crop_name = str(crop_name).strip()
        if not crop_name:
            return None
        
        existing = self.db.query(CropRecord).filter(
            CropRecord.crop_name.ilike(crop_name)
        ).first()
        
        if existing:
            return existing.crop_id
        
        # Generate new crop_id
        last_crop = self.db.query(CropRecord).order_by(CropRecord.crop_id.desc()).first()
        if last_crop:
            match = re.search(r'(\d+)$', last_crop.crop_id)
            next_num = int(match.group(1)) + 1 if match else 1
        else:
            next_num = 1
        
        crop_id = f"CR_{str(next_num).zfill(3)}"
        
        try:
            new_crop = CropRecord(
                crop_id=crop_id,
                crop_name=crop_name,
                category_id=100001
            )
            self.db.add(new_crop)
            self.db.commit()
            self.processed_counts['master_entities_created'] += 1
            self.logger.info(f"Created crop: {crop_id} ({crop_name})")
            return crop_id
        except Exception as e:
            self.db.rollback()
            existing = self.db.query(CropRecord).filter(
                CropRecord.crop_name.ilike(crop_name)
            ).first()
            if existing:
                return existing.crop_id
            raise
    
    def get_or_create_variety(self, variety_name: str, crop_id: str) -> str:
        """Get or create variety and return variety_id"""
        if not variety_name or pd.isna(variety_name) or not crop_id:
            return None
        
        variety_name = str(variety_name).strip()
        if not variety_name:
            return None
        
        # Normalize variety name (US -> USRH, but skip if already USRH)
        variety_upper = variety_name.upper()
        if variety_upper.startswith('US') and not variety_upper.startswith('USRH'):
            # Only normalize if it starts with US but not USRH (e.g., US04, US 04, US-04)
            remaining = variety_name[2:].strip().lstrip(' -_')
            variety_name = f"USRH-{remaining}"
        
        existing = self.db.query(VarietyRecord).filter(
            VarietyRecord.variety_name.ilike(variety_name),
            VarietyRecord.crop_id == crop_id
        ).first()
        
        if existing:
            return existing.variety_id
        
        # Generate new variety_id
        last_variety = self.db.query(VarietyRecord).order_by(VarietyRecord.variety_id.desc()).first()
        if last_variety:
            match = re.search(r'(\d+)$', last_variety.variety_id)
            next_num = int(match.group(1)) + 1 if match else 1001
        else:
            next_num = 1001
        
        variety_id = f"VR_{str(next_num).zfill(4)}"
        
        try:
            new_variety = VarietyRecord(
                variety_id=variety_id,
                variety_name=variety_name,
                crop_id=crop_id,
                category_id=100003
            )
            self.db.add(new_variety)
            self.db.commit()
            self.processed_counts['master_entities_created'] += 1
            self.logger.info(f"Created variety: {variety_id} ({variety_name})")
            return variety_id
        except Exception as e:
            self.db.rollback()
            existing = self.db.query(VarietyRecord).filter(
                VarietyRecord.variety_name.ilike(variety_name),
                VarietyRecord.crop_id == crop_id
            ).first()
            if existing:
                return existing.variety_id
            raise
    
    def get_location_data(self, village_name: str) -> dict:
        """
        Get location data (location_id, state, mandal, district) from LocationRecord table
        Uses location_id as unique identifier
        Returns dict with location_id, state, mandal, district, or None if not found
        """
        if not village_name or pd.isna(village_name):
            return None
        
        village_name = str(village_name).strip()
        if not village_name:
            return None
        
        # Try to find by village name (case-insensitive)
        location = self.db.query(LocationRecord).filter(
            LocationRecord.village.ilike(village_name)
        ).first()
        
        if location:
            return {
                'location_id': str(location.location_id) if location.location_id else None,
                'state': str(location.state) if location.state else None,
                'mandal': str(location.mandal) if location.mandal else None,
                'district': str(location.district) if location.district else None,
                'village': str(location.village) if location.village else village_name
            }
        
        # If village_name looks like a location_id (starts with L_), try to find by location_id
        if village_name.upper().startswith('L_'):
            location = self.db.query(LocationRecord).filter(
                LocationRecord.location_id == village_name.upper()
            ).first()
            
            if location:
                return {
                    'location_id': str(location.location_id) if location.location_id else None,
                    'state': str(location.state) if location.state else None,
                    'mandal': str(location.mandal) if location.mandal else None,
                    'district': str(location.district) if location.district else None,
                    'village': str(location.village) if location.village else village_name
                }
            # If found by location_id pattern but not in DB, return location_id only
            return {
                'location_id': village_name.upper(),
                'state': None,
                'mandal': None,
                'district': None,
                'village': village_name
            }
        
        return None
    
    def upsert_master_entities(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add season_id, crop_id, variety_id, location_id, state, mandal, district to DataFrame
        Uses LocationRecord table with location_id as unique identifier to get state, mandal, district
        OPTIMIZED: Uses bulk operations instead of row-by-row processing
        """
        self.logger.info("Upserting master entities...")
        self.logger.info(f"Processing {len(df)} rows...")
        
        # Step 1: Extract unique values
        unique_seasons = df['season'].dropna().unique() if 'season' in df.columns else []
        unique_crops = df['crop'].dropna().unique() if 'crop' in df.columns else []
        
        # Handle unique_varieties more carefully
        if 'variety' in df.columns and 'crop' in df.columns:
            unique_varieties = df[['variety', 'crop']].dropna(subset=['variety']).drop_duplicates()
        else:
            unique_varieties = pd.DataFrame(columns=['variety', 'crop'])
        
        unique_villages = []
        if 'village' in df.columns:
            unique_villages.extend(df['village'].dropna().unique().tolist())
        if 'location' in df.columns:
            unique_villages.extend(df['location'].dropna().unique().tolist())
        unique_villages = list(set([str(v).strip() for v in unique_villages if v and str(v) != 'nan']))
        
        self.logger.info(f"Found {len(unique_seasons)} unique seasons, {len(unique_crops)} unique crops, "
                        f"{len(unique_varieties)} unique variety-crop pairs, {len(unique_villages)} unique villages")
        
        # Step 2: Bulk fetch existing entities
        season_map = {}
        if unique_seasons:
            existing_seasons = self.db.query(SeasonRecord).filter(
                SeasonRecord.season_id.in_([s.replace(' ', '_').replace('-', '_').upper() for s in unique_seasons])
            ).all()
            season_map = {s.season_id: s.season_id for s in existing_seasons}
        
        crop_map = {}
        if unique_crops:
            existing_crops = self.db.query(CropRecord).filter(
                CropRecord.crop_name.in_([str(c).strip() for c in unique_crops])
            ).all()
            crop_map = {c.crop_name.lower(): c.crop_id for c in existing_crops}
        
        variety_map = {}
        if not unique_varieties.empty and len(unique_varieties) > 0:
            # Build variety -> crop mapping first
            variety_crop_map = {}
            for _, row in unique_varieties.iterrows():
                try:
                    var_name = str(row['variety']).strip()
                    crop_name = str(row['crop']).strip()
                    if var_name and crop_name and var_name != 'nan' and crop_name != 'nan':
                        variety_crop_map[var_name.lower()] = crop_name.lower()
                except Exception as e:
                    self.logger.warning(f"Error processing variety row: {str(e)}")
                    continue
            
            # Bulk fetch all existing varieties for the crops we have
            crop_ids_list = list(set(crop_map.values()))
            if crop_ids_list:
                try:
                    existing_varieties_all = self.db.query(VarietyRecord).filter(
                        VarietyRecord.crop_id.in_(crop_ids_list)
                    ).all()
                    
                    # Build lookup map
                    for var in existing_varieties_all:
                        key = (var.variety_name.lower(), var.crop_id)
                        variety_map[key] = var.variety_id
                except Exception as e:
                    self.logger.warning(f"Error fetching existing varieties: {str(e)}")
        
        # Step 3: Bulk fetch locations and create missing ones
        location_map = {}
        locations_to_create = []
        
        if unique_villages:
            # Convert villages to lowercase for comparison
            unique_villages_lower = [str(v).strip().lower() for v in unique_villages if v]
            
            # Fetch existing locations (case-insensitive comparison)
            existing_locations = self.db.query(LocationRecord).all()
            existing_villages_lower = {str(loc.village).lower(): loc for loc in existing_locations}
            
            # Build location map from existing locations
            for village_lower, loc in existing_villages_lower.items():
                location_map[village_lower] = {
                    'location_id': str(loc.location_id) if loc.location_id else None,
                    'state': str(loc.state).lower() if loc.state else None,
                    'mandal': str(loc.mandal).lower() if loc.mandal else None,
                    'district': str(loc.district).lower() if loc.district else None,
                    'village': str(loc.village).lower() if loc.village else None
                }
            
            # Find missing locations and prepare to create them
            # Extract unique location data from DataFrame for missing villages
            missing_villages = []
            village_location_data = {}  # Map village -> {district, state, mandal}
            
            # Build a set of unique village-location combinations from DataFrame
            location_combinations = {}
            for _, row in df.iterrows():
                village = str(row.get('village') or row.get('location') or '').strip()
                if village:
                    village_lower = village.lower()
                    if village_lower not in location_map:
                        # Store location data (use first occurrence for each village)
                        if village_lower not in location_combinations:
                            location_combinations[village_lower] = {
                                'village': village.lower(),
                                'district': str(row.get('district', '')).strip().lower() if row.get('district') and str(row.get('district')) != 'nan' else None,
                                'state': str(row.get('state', '')).strip().lower() if row.get('state') and str(row.get('state')) != 'nan' else None,
                                'mandal': str(row.get('mandal', '')).strip().lower() if row.get('mandal') and str(row.get('mandal')) != 'nan' else None
                            }
            
            # Add to village_location_data
            for village_lower, loc_data in location_combinations.items():
                village_location_data[village_lower] = loc_data
                missing_villages.append(village_lower)
            
            # Generate location_id for missing locations
            if missing_villages:
                # Get the highest existing location_id
                last_location = self.db.query(LocationRecord).order_by(LocationRecord.location_id.desc()).first()
                next_loc_num = 1
                
                if last_location:
                    # Extract number from location_id (e.g., L_00001 -> 1)
                    match = re.search(r'L_(\d+)', str(last_location.location_id))
                    if match:
                        next_loc_num = int(match.group(1)) + 1
                    else:
                        # Try to find any numeric pattern
                        match = re.search(r'(\d+)$', str(last_location.location_id))
                        if match:
                            next_loc_num = int(match.group(1)) + 1
                
                # Create location records for missing villages
                for village_lower in missing_villages:
                    loc_data = village_location_data[village_lower]
                    location_id = f"L_{str(next_loc_num).zfill(5)}"
                    
                    locations_to_create.append(LocationRecord(
                        location_id=location_id,
                        village=loc_data['village'],
                        district=loc_data['district'],
                        state=loc_data['state'],
                        mandal=loc_data['mandal'],
                        category_id=100004  # Default category for locations
                    ))
                    
                    # Add to location_map
                    location_map[village_lower] = {
                        'location_id': location_id,
                        'state': loc_data['state'],
                        'mandal': loc_data['mandal'],
                        'district': loc_data['district'],
                        'village': loc_data['village']
                    }
                    
                    next_loc_num += 1
                
                # Bulk insert new locations
                if locations_to_create:
                    self.db.bulk_save_objects(locations_to_create)
                    self.db.commit()
                    self.processed_counts['master_entities_created'] += len(locations_to_create)
                    self.logger.info(f"Created {len(locations_to_create)} new location records")
        
        # Step 4: Create missing entities in bulk
        seasons_to_create = []
        for season_name in unique_seasons:
            season_id = str(season_name).strip().replace(' ', '_').replace('-', '_').upper()
            if season_id not in season_map:
                seasons_to_create.append(SeasonRecord(season_id=season_id, season_name=str(season_name).strip()))
        
        crops_to_create = []
        last_crop = self.db.query(CropRecord).order_by(CropRecord.crop_id.desc()).first()
        next_crop_num = 1
        if last_crop:
            match = re.search(r'(\d+)$', last_crop.crop_id)
            next_crop_num = int(match.group(1)) + 1 if match else 1
        
        for crop_name in unique_crops:
            crop_name_str = str(crop_name).strip()
            if crop_name_str.lower() not in crop_map:
                crop_id = f"CR_{str(next_crop_num).zfill(3)}"
                crops_to_create.append(CropRecord(crop_id=crop_id, crop_name=crop_name_str, category_id=100001))
                crop_map[crop_name_str.lower()] = crop_id
                next_crop_num += 1
        
        varieties_to_create = []
        last_variety = self.db.query(VarietyRecord).order_by(VarietyRecord.variety_id.desc()).first()
        next_var_num = 1001
        if last_variety:
            match = re.search(r'(\d+)$', last_variety.variety_id)
            next_var_num = int(match.group(1)) + 1 if match else 1001
        
        for _, row in unique_varieties.iterrows():
            var_name = str(row['variety']).strip()
            crop_name = str(row['crop']).strip()
            crop_id = crop_map.get(crop_name.lower())
            if crop_id and (var_name.lower(), crop_id) not in variety_map:
                # Normalize variety name
                var_upper = var_name.upper()
                if var_upper.startswith('US') and not var_upper.startswith('USRH'):
                    remaining = var_name[2:].strip().lstrip(' -_')
                    var_name = f"USRH-{remaining}"
                
                variety_id = f"VR_{str(next_var_num).zfill(4)}"
                varieties_to_create.append(VarietyRecord(
                    variety_id=variety_id,
                    variety_name=var_name,
                    crop_id=crop_id,
                    category_id=100003
                ))
                variety_map[(var_name.lower(), crop_id)] = variety_id
                next_var_num += 1
        
        # Bulk insert new entities
        if seasons_to_create:
            self.db.bulk_save_objects(seasons_to_create)
            self.processed_counts['master_entities_created'] += len(seasons_to_create)
            self.logger.info(f"Created {len(seasons_to_create)} new seasons")
        
        if crops_to_create:
            self.db.bulk_save_objects(crops_to_create)
            self.processed_counts['master_entities_created'] += len(crops_to_create)
            self.logger.info(f"Created {len(crops_to_create)} new crops")
        
        if varieties_to_create:
            self.db.bulk_save_objects(varieties_to_create)
            self.processed_counts['master_entities_created'] += len(varieties_to_create)
            self.logger.info(f"Created {len(varieties_to_create)} new varieties")
        
        if seasons_to_create or crops_to_create or varieties_to_create:
            self.db.commit()
        
        # Step 5: Map DataFrame rows to IDs
        self.logger.info("Mapping rows to entity IDs...")
        # Reset index to ensure sequential indexing (avoids broadcasting issues)
        original_length = len(df)
        df = df.reset_index(drop=True)
        
        if len(df) != original_length:
            self.logger.warning(f"DataFrame length changed after reset_index: {original_length} -> {len(df)}")
        
        # Store original index for reference
        df_index = df.index.copy()
        
        season_ids = []
        crop_ids = []
        variety_ids = []
        location_ids = []
        states = []
        mandals = []
        districts = []
        villages = []
        
        total_rows = len(df)
        self.logger.info(f"Starting to map {total_rows} rows to entity IDs")
        # Use iloc to iterate by position (avoids index issues)
        for pos in range(total_rows):
            try:
                # Progress logging
                if (pos + 1) % 1000 == 0:
                    progress = ((pos + 1) / total_rows) * 100
                    self.logger.info(f"  Progress: {pos + 1}/{total_rows} rows mapped ({progress:.1f}%)")
                
                # Get row by position
                row = df.iloc[pos]
                
                season_name = row.get('season')
                crop_name = row.get('crop')
                variety_name = row.get('variety')
                village_name = str(row.get('village') or row.get('location') or '').strip()
                
                # Map to IDs
                season_id = None
                if season_name:
                    season_id = str(season_name).strip().replace(' ', '_').replace('-', '_').upper()
                    if season_id not in season_map:
                        season_id = None
                
                crop_id = crop_map.get(str(crop_name).strip().lower()) if crop_name else None
                
                variety_id = None
                if variety_name and crop_id:
                    # Normalize variety name (US -> USRH) before lookup
                    var_name_normalized = str(variety_name).strip()
                    var_upper = var_name_normalized.upper()
                    if var_upper.startswith('US') and not var_upper.startswith('USRH'):
                        remaining = var_name_normalized[2:].strip().lstrip(' -_')
                        var_name_normalized = f"USRH-{remaining}"
                    
                    variety_id = variety_map.get((var_name_normalized.lower(), crop_id))
                
                # Get location data (all values should be lowercase)
                location_data = location_map.get(village_name.lower()) if village_name else None
                
                if location_data:
                    location_ids.append(location_data.get('location_id'))
                    # Use lowercase values from location_data or from row
                    state_val = location_data.get('state')
                    if not state_val and row.get('state'):
                        state_val = str(row.get('state', '')).strip().lower()
                    states.append(state_val)
                    
                    mandal_val = location_data.get('mandal')
                    if not mandal_val and row.get('mandal'):
                        mandal_val = str(row.get('mandal', '')).strip().lower()
                    mandals.append(mandal_val)
                    
                    district_val = location_data.get('district')
                    if not district_val and row.get('district'):
                        district_val = str(row.get('district', '')).strip().lower()
                    districts.append(district_val)
                    
                    villages.append(location_data.get('village') or village_name.lower())
                else:
                    location_ids.append(None)
                    # Convert to lowercase
                    states.append(str(row.get('state', '')).strip().lower() if row.get('state') else None)
                    mandals.append(str(row.get('mandal', '')).strip().lower() if row.get('mandal') else None)
                    districts.append(str(row.get('district', '')).strip().lower() if row.get('district') else None)
                    villages.append(village_name.lower() if village_name else None)
                
                season_ids.append(season_id)
                crop_ids.append(crop_id)
                variety_ids.append(variety_id)
            except Exception as e:
                self.logger.error(f"Error processing row {pos}: {str(e)}")
                import traceback
                self.logger.error(f"Traceback: {traceback.format_exc()}")
                # Append None values to maintain list length
                season_ids.append(None)
                crop_ids.append(None)
                variety_ids.append(None)
                location_ids.append(None)
                states.append(None)
                mandals.append(None)
                districts.append(None)
                villages.append(None)
        
        # Verify all lists have the same length
        list_lengths = {
            'season_ids': len(season_ids),
            'crop_ids': len(crop_ids),
            'variety_ids': len(variety_ids),
            'location_ids': len(location_ids),
            'states': len(states),
            'mandals': len(mandals),
            'districts': len(districts),
            'villages': len(villages)
        }
        
        self.logger.info(f"List lengths: {list_lengths}")
        self.logger.info(f"DataFrame length: {len(df)}")
        self.logger.info(f"DataFrame index: {df.index.tolist()[:10]}... (showing first 10)")
        
        if len(set(list_lengths.values())) > 1:
            self.logger.error(f"List length mismatch: {list_lengths}")
            self.logger.error(f"DataFrame length: {len(df)}")
            raise ValueError(f"List lengths don't match: {list_lengths}")
        
        if len(season_ids) != len(df):
            self.logger.error(f"List length ({len(season_ids)}) doesn't match DataFrame length ({len(df)})")
            raise ValueError(f"List length ({len(season_ids)}) doesn't match DataFrame length ({len(df)})")
        
        # Convert lists to Series with DataFrame index to avoid broadcasting issues
        try:
            df['season_id'] = pd.Series(season_ids, index=df.index)
            df['crop_id'] = pd.Series(crop_ids, index=df.index)
            df['variety_id'] = pd.Series(variety_ids, index=df.index)
            df['location_id'] = pd.Series(location_ids, index=df.index)
            df['state'] = pd.Series(states, index=df.index)
            df['mandal'] = pd.Series(mandals, index=df.index)
            df['district'] = pd.Series(districts, index=df.index)
            df['village'] = pd.Series(villages, index=df.index)
            self.logger.info("Successfully assigned all columns to DataFrame")
        except Exception as e:
            self.logger.error(f"Error assigning columns to DataFrame: {str(e)}")
            self.logger.error(f"Error type: {type(e).__name__}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            raise
        
        self.logger.info(f"Master entity upsert complete. Created {self.processed_counts['master_entities_created']} new entities")
        self.logger.info(f"Location data loaded: {len([x for x in location_ids if x])} locations found in database")
        return df
    
    def insert_planning_data(self, df: pd.DataFrame, plan_revision_version: str):
        """Insert data into supply_chain_planning table using bulk operations"""
        import time
        self.logger.info("=" * 80)
        self.logger.info("PREPARING DATA FOR DATABASE UPLOAD...")
        self.logger.info(f"Total rows to process: {len(df)}")
        self.logger.info("=" * 80)
        
        insert_records = []
        total_rows = len(df)
        
        self.logger.info("Building insert records...")
        for idx, row in df.iterrows():
            try:
                # Build insert record
                village_name = str(row.get('village', '')) or str(row.get('location', ''))
                state_value = str(row.get('state', '')) if row.get('state') and str(row.get('state')) != 'nan' else None
                mandal_value = str(row.get('mandal', '')) if row.get('mandal') and str(row.get('mandal')) != 'nan' else None
                district_value = str(row.get('district', '')) if row.get('district') and str(row.get('district')) != 'nan' else None
                
                insert_record = {
                    'plan_revision_version': plan_revision_version,
                    'season': str(row.get('season', '')),
                    'season_id': str(row.get('season_id', '')) if row.get('season_id') else None,
                    'crop': str(row.get('crop', '')),
                    'crop_id': str(row.get('crop_id', '')) if row.get('crop_id') else None,
                    'variety': str(row.get('variety', '')),
                    'variety_id': str(row.get('variety_id', '')) if row.get('variety_id') else None,
                    'village': village_name,
                    'location_id': str(row.get('location_id', '')) if row.get('location_id') else None,
                    'location': village_name,
                    'state': state_value,
                    'mandal': mandal_value,
                    'district': district_value,
                    'hybrid': str(row.get('hybrid', '')) if row.get('hybrid') else None,
                    'net_acres_current': float(row.get('net_acres_current', 0)) if pd.notna(row.get('net_acres_current')) else None,
                    'productivity': float(row.get('productivity', 0)) if pd.notna(row.get('productivity')) else None,
                    'production_allocation': float(row.get('production_allocation', 0)) if pd.notna(row.get('production_allocation')) else None,
                    'category_id': 100007,
                    'created_at': datetime.utcnow(),
                    'updated_at': datetime.utcnow()
                }
                
                insert_records.append(insert_record)
                
                # Progress logging
                if (idx + 1) % 1000 == 0:
                    progress = ((idx + 1) / total_rows) * 100
                    self.logger.info(f"  Progress: {idx + 1}/{total_rows} records prepared ({progress:.1f}%)")
            except Exception as e:
                self.logger.warning(f"Error preparing record {idx}: {str(e)}")
                continue
        
        self.logger.info("=" * 80)
        self.logger.info(f"DATA PREPARATION COMPLETE")
        self.logger.info(f"  ✓ Successfully prepared: {len(insert_records)} records")
        self.logger.info("=" * 80)
        
        # Bulk insert using SQLAlchemy bulk operations
        if insert_records:
            self.logger.info("=" * 80)
            self.logger.info("STARTING DATABASE UPLOAD...")
            self.logger.info("=" * 80)
            self.logger.info(f"  📤 STATUS: UPLOADING TO DATABASE")
            self.logger.info(f"  📊 Records to upload: {len(insert_records)}")
            self.logger.info(f"  🗄️  Table: operations.supply_chain_planning")
            self.logger.info(f"  📝 Version: {plan_revision_version}")
            self.logger.info(f"  ⏱️  Starting upload at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.logger.info("=" * 80)
            
            start_time = time.time()
            
            # Use raw SQL with execute_values for maximum performance (faster than bulk_insert_mappings)
            try:
                # Get connection for raw SQL bulk insert
                conn = get_connection()
                cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                
                # Prepare data as tuples for execute_values
                columns = list(insert_records[0].keys())
                columns_str = ', '.join(columns)
                
                # Convert records to tuples in the same order as columns
                rows_to_insert = []
                for record in insert_records:
                    row_tuple = tuple(record.get(col) for col in columns)
                    rows_to_insert.append(row_tuple)
                
                self.logger.info("  🔄 Executing bulk insert operation (using execute_values)...")
                self.logger.info(f"  📦 Batch size: 1000 rows per batch")
                
                # Use execute_values for bulk insert (much faster than bulk_insert_mappings)
                psycopg2.extras.execute_values(
                    cur,
                    f"INSERT INTO operations.supply_chain_planning ({columns_str}) VALUES %s",
                    rows_to_insert,
                    template=None,
                    page_size=1000  # Insert 1000 rows at a time
                )
                
                insert_time = time.time() - start_time
                
                self.logger.info("  💾 Committing transaction to database...")
                commit_start = time.time()
                conn.commit()
                commit_time = time.time() - commit_start
                cur.close()
                release_connection(conn)
                
                self.logger.info("  ✓ Transaction committed successfully")
                
                self.processed_counts['rows_inserted'] = len(insert_records)
                self.processed_counts['rows_skipped'] = 0
                
                self.logger.info("=" * 80)
                self.logger.info("DATABASE UPLOAD COMPLETE!")
                self.logger.info("=" * 80)
                self.logger.info(f"  ✓ Bulk insert executed successfully")
                self.logger.info(f"  ✓ Records inserted: {len(insert_records)}")
                self.logger.info(f"  ✓ Insert time: {insert_time:.2f} seconds")
                self.logger.info(f"  ✓ Commit time: {commit_time:.2f} seconds")
                self.logger.info(f"  ✓ Total time: {insert_time + commit_time:.2f} seconds")
                self.logger.info(f"  ✓ Records per second: {len(insert_records)/(insert_time + commit_time):.0f}")
                self.logger.info("=" * 80)
                
                # Verify upload by checking database
                self.logger.info("=" * 80)
                self.logger.info("VERIFYING DATABASE UPLOAD...")
                self.logger.info("=" * 80)
                try:
                    verify_conn = get_connection()
                    verify_cur = verify_conn.cursor()
                    verify_cur.execute("""
                        SELECT COUNT(*) as count 
                        FROM operations.supply_chain_planning 
                        WHERE plan_revision_version = %s
                    """, (plan_revision_version,))
                    db_count = verify_cur.fetchone()[0]
                    verify_cur.close()
                    release_connection(verify_conn)
                    
                    self.logger.info(f"  ✓ Database verification complete")
                    self.logger.info(f"  ✓ Found {db_count} rows in database for version '{plan_revision_version}'")
                    self.logger.info(f"  ✓ Expected: {len(insert_records)} rows inserted")
                    
                    if db_count == len(insert_records):
                        self.logger.info("  ✓ SUCCESS: Database row count matches inserted count!")
                    elif db_count > 0:
                        self.logger.warning(f"  ⚠ WARNING: Database has {db_count} rows but expected {len(insert_records)}")
                    else:
                        self.logger.error(f"  ✗ ERROR: No rows found in database! Expected {len(insert_records)} rows")
                        raise Exception(f"Database verification failed: Expected {len(insert_records)} rows but found {db_count}")
                        
                except Exception as e:
                    self.logger.error(f"  ✗ ERROR: Could not verify database upload: {str(e)}")
                    raise
                
                self.logger.info("=" * 80)
                
            except Exception as e:
                if 'conn' in locals():
                    try:
                        conn.rollback()
                        cur.close()
                        release_connection(conn)
                    except:
                        pass
                self.logger.error(f"Error during bulk insert: {str(e)}")
                self.logger.error(f"Error type: {type(e).__name__}")
                import traceback
                self.logger.error(f"Traceback: {traceback.format_exc()}")
                raise
        else:
            self.logger.warning("No records to insert")
            self.processed_counts['rows_inserted'] = 0
            self.processed_counts['rows_skipped'] = 0
    
    def load_and_process(self, file_path: str, plan_revision_version: str) -> dict:
        """Main processing function"""
        try:
            self.logger.info("=" * 80)
            self.logger.info("PHASE 1: Loading Excel file...")
            self.logger.info("=" * 80)
            # Load Excel
            df = self.load_excel(file_path)
            self.processed_counts['rows_processed'] = len(df)
            self.logger.info(f"✓ Loaded {len(df)} rows from Excel file")
            
            self.logger.info("=" * 80)
            self.logger.info("PHASE 2: Normalizing columns...")
            self.logger.info("=" * 80)
            # Normalize columns
            df = self.normalize_columns(df)
            self.logger.info(f"✓ Column normalization complete. Columns: {list(df.columns)}")
            
            self.logger.info("=" * 80)
            self.logger.info("PHASE 3: Upserting master entities...")
            self.logger.info("=" * 80)
            # Upsert master entities and add IDs
            df = self.upsert_master_entities(df)
            self.logger.info("✓ Master entities upsert complete")
            
            self.logger.info("=" * 80)
            self.logger.info("PHASE 4: Uploading to database...")
            self.logger.info("=" * 80)
            # Insert planning data
            self.insert_planning_data(df, plan_revision_version)
            self.logger.info("✓ Database upload complete")
            
            return {
                'success': True,
                'rows_processed': self.processed_counts['rows_processed'],
                'rows_inserted': self.processed_counts['rows_inserted'],
                'rows_skipped': self.processed_counts['rows_skipped'],
                'master_entities_created': self.processed_counts['master_entities_created'],
                'message': f"Successfully processed {self.processed_counts['rows_inserted']} rows"
            }
        except Exception as e:
            self.logger.error(f"Error in processing: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'rows_processed': self.processed_counts.get('rows_processed', 0),
                'rows_inserted': self.processed_counts.get('rows_inserted', 0)
            }


def main():
    """Main function to load planning data with flexible file path."""
    
    # Default path
    default_path = r"C:\Users\madan\Downloads\USRH-24 Manual village level revised.xlsx"
    
    # Check if file path provided as command line argument
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    else:
        file_path = default_path
    
    # Also check for a copied version in the project directory
    alternative_paths = [
        file_path,
        os.path.join(os.getcwd(), "USRH-24 Manual village level revised.xlsx"),
        os.path.join(os.getcwd(), "temp_planning_data.xlsx"),
        r"C:\Users\madan\Downloads\USRH-24 Manual village level revised.xlsx",
    ]
    
    # Find the first accessible file
    found_path = None
    for path in alternative_paths:
        if os.path.exists(path):
            try:
                # Try to open it
                test = open(path, 'rb')
                test.close()
                found_path = path
                logger.info(f"Found accessible file at: {path}")
                break
            except PermissionError:
                logger.warning(f"File exists but is locked: {path}")
                continue
    
    if not found_path:
        print("\n[ERROR] Could not find an accessible Excel file.")
        print("\nPlease do one of the following:")
        print("1. Close Excel and OneDrive, then run again")
        print("2. Copy the file to the project directory and run:")
        print(f"   python load_planning_data_alternative.py <path_to_file>")
        print("3. Copy the file to Desktop and update the script")
        sys.exit(1)
    
    plan_revision_version = "v4.0-Manual"
    
    db = SessionLocal()
    try:
        logger.info("=" * 80)
        logger.info("Starting Planning Data Load Process")
        logger.info(f"File: {found_path}")
        logger.info(f"Plan Revision Version: {plan_revision_version}")
        logger.info("=" * 80)
        
        # Delete existing data for this version
        logger.info(f"Deleting existing data for version: {plan_revision_version}")
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            cur.execute("""
                DELETE FROM operations.supply_chain_planning 
                WHERE plan_revision_version = %s
            """, (plan_revision_version,))
            deleted_count = cur.rowcount
            conn.commit()
            logger.info(f"Deleted {deleted_count} existing rows for version {plan_revision_version}")
        except Exception as e:
            logger.warning(f"Error deleting existing data: {e}")
            conn.rollback()
        finally:
            cur.close()
            release_connection(conn)
        
        # Initialize loader and process
        logger.info("=" * 80)
        logger.info("STEP 1: Initializing data loader...")
        logger.info("=" * 80)
        loader = PlanningDataLoader(db)
        
        logger.info("=" * 80)
        logger.info("STEP 2: Starting data load and processing...")
        logger.info("=" * 80)
        result = loader.load_and_process(found_path, plan_revision_version)
        
        # Print results
        logger.info("=" * 80)
        logger.info("STEP 3: Processing Complete!")
        logger.info("=" * 80)
        logger.info(f"Success: {result.get('success', False)}")
        logger.info(f"Rows Processed: {result.get('rows_processed', 0)}")
        logger.info(f"Rows Inserted: {result.get('rows_inserted', 0)}")
        logger.info(f"Rows Skipped: {result.get('rows_skipped', 0)}")
        logger.info(f"Master Entities Created: {result.get('master_entities_created', 0)}")
        
        if result.get('error'):
            logger.error("=" * 80)
            logger.error("DATABASE UPLOAD FAILED!")
            logger.error("=" * 80)
            logger.error(f"Error: {result.get('error')}")
            print("\n[ERROR] Failed to upload data to database!")
            print(f"   Error: {result.get('error')}")
            sys.exit(1)
        else:
            logger.info("=" * 80)
            logger.info("DATABASE UPLOAD SUCCESSFUL!")
            logger.info("=" * 80)
            logger.info(f"Message: {result.get('message', 'N/A')}")
            
            # Final verification
            logger.info("=" * 80)
            logger.info("STEP 4: Final database verification...")
            logger.info("=" * 80)
            try:
                verify_conn = get_connection()
                verify_cur = verify_conn.cursor()
                verify_cur.execute("""
                    SELECT COUNT(*) as count 
                    FROM operations.supply_chain_planning 
                    WHERE plan_revision_version = %s
                """, (plan_revision_version,))
                db_count = verify_cur.fetchone()[0]
                verify_cur.close()
                release_connection(verify_conn)
                
                logger.info(f"✓ Final verification: Found {db_count} rows in database")
                logger.info(f"✓ Expected: {result.get('rows_inserted', 0)} rows inserted")
                
                if db_count == result.get('rows_inserted', 0):
                    logger.info("✓ SUCCESS: Database row count matches inserted count!")
                elif db_count > 0:
                    logger.warning(f"⚠ WARNING: Database has {db_count} rows but expected {result.get('rows_inserted', 0)}")
                else:
                    logger.error(f"✗ ERROR: No rows found in database! Expected {result.get('rows_inserted', 0)} rows")
                    
            except Exception as e:
                logger.warning(f"Could not perform final verification: {str(e)}")
            
            logger.info("=" * 80)
            print("\n[SUCCESS] Planning data loaded successfully!")
            print(f"   ✓ Inserted {result.get('rows_inserted', 0)} rows into database")
            print(f"   ✓ Created {result.get('master_entities_created', 0)} master entities")
            print(f"   ✓ Plan Revision Version: {plan_revision_version}")
            
    except FileNotFoundError as e:
        logger.error(f"File not found: {found_path}")
        logger.error(f"Error: {str(e)}")
        print(f"\n[ERROR] File not found at {found_path}")
        sys.exit(1)
    except PermissionError as e:
        logger.error(f"Permission error: {str(e)}")
        print(f"\n[ERROR] Permission Error: {str(e)}")
        print("\n[INFO] Solution:")
        print("   1. Close the Excel file if it's open")
        print("   2. Wait for OneDrive sync to complete")
        print("   3. Copy the file to a different location (e.g., Desktop)")
        print("   4. Run: python load_planning_data_alternative.py <new_path>")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        print(f"\n[ERROR] Error: {str(e)}")
        sys.exit(1)
    finally:
        db.close()
        logger.info("Database session closed")


if __name__ == "__main__":
    main()
