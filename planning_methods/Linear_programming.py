import pandas as pd
import numpy as np
from typing import Dict, Tuple, List, Optional
import warnings
import re
import os
import tempfile
import logging
from datetime import datetime
from pulp import LpMaximize, LpMinimize, LpProblem, LpVariable, lpSum, LpStatus
from sqlalchemy.orm import Session
from sqlalchemy import func, Float, text
from sqlalchemy.exc import IntegrityError

from models.db_models import (
    SeasonCropInspectionFinal,
    SupplyChainPlanning,
    CropRecord,
    VarietyRecord,
    LocationRecord,
    SeasonRecord
)
from core.db import get_db

warnings.filterwarnings('ignore')

logger = logging.getLogger(__name__)


class SupplyChainPlanner:
    def __init__(self, db: Session):
        """
        Initialize the Supply Chain Planner with database connection

        Args:
            db (Session): SQLAlchemy database session (required)
        """
        if db is None:
            raise ValueError("Database session (db) is required")
        
        self.db = db
        self.output_folder = r"D:\Supply_Chain_Planning\Planning\outputs"
        self.target_production = 10000000  # 10,000 MT in Kgs (default, will be overridden by state-variety targets)
        
        # Define target production per state per variety for 2024-2025 plan (from manual input - new image)
        # Values are in MT, will be converted to Kgs (multiply by 1000)
        # Format: {state: {variety: target_MT}}
        self.state_variety_targets_2024 = {
            'Odisha': {
                'USRH-05': 50,
                'USRH-24': 3360,
                'USRH-26': 100,
                'USRH-31': 626
            },
            'Chattissgarh': {
                'USRH-24': 80,
                'USRH-31': 1472
            },
            'Telangana': {
                'USRH-04': 1000,
                'USRH-05': 150,
                'USRH-26': 1200,
                'USRH-31': 402
            },
            'Karnataka': {
                'USRH-24': 1560
            }
        }
        
        # Define target production per state per variety for 2025-2026 plan (keep existing targets)
        self.state_variety_targets_2025 = {
            'Odisha': {
                'USRH-05': 75,
                'USRH-24': 3360,
                'USRH-26': 136,
                'USRH-31': 626
            },
            'Chattissgarh': {
                'USRH-10': 126,
                'USRH-22': 100,
                'USRH-24': 79,
                'USRH-31': 1472
            },
            'Warangal': {
                # All empty in manual
            },
            'Karimnagar': {
                'USRH-04': 1000,
                'USRH-05': 175,
                'USRH-10': 74,
                'USRH-22': 0,
                'USRH-26': 2114,
                'USRH-31': 402
            },
            'Karnataka': {
                'USRH-24': 1561
            }
        }
        
        # Initialize empty - will be set based on plan year
        self.state_variety_targets = {}
        self.state_variety_targets_kgs = {}
        self.targets_set_from_ui = False  # Flag to track if targets are set from UI selections
        
        # Load data from database only
        self.load_from_database()

    def _safe_to_numeric(self, series: pd.Series) -> pd.Series:
        """
        Instance helper: safely convert a Series to float numbers.
        Removes commas, handles 'nan' strings, coerces non-numeric to 0.0.
        """
        if series is None:
            return pd.Series([], dtype=float)

        s = series.astype(str).str.replace(',', '', regex=False).replace({'nan': np.nan, 'NaN': np.nan})
        return pd.to_numeric(s, errors='coerce').fillna(0.0).astype(float)

    def extract_year_from_season(self, season: str) -> int:
        """Extract 4-digit year from season string"""
        if not season:
            return None
        season = str(season).strip()
        
        # Pattern 1: Contains 4-digit year (e.g., "RABI 2024", "2024-25")
        year_match = re.search(r'20[0-9]{2}', season)
        if year_match:
            return int(year_match.group())
        
        # Pattern 2: Two-digit year format (e.g., "24-25", "23-24")
        two_digit_match = re.search(r'([0-9]{2})-([0-9]{2})', season)
        if two_digit_match:
            return 2000 + int(two_digit_match.group(1))
        
        return None

    def load_from_database(self):
        """
        Load data from database (operations.season_crop_inspection_final table)
        and convert to the same format as CSV loading
        Uses raw SQL with proper type casting to avoid PostgreSQL type conversion issues
        """
        print("Loading data from database (schema: operations)...")
        
        # Use raw SQL query - cast all columns to TEXT to avoid PostgreSQL type issues
        # Then convert to appropriate types in Python
        # Added state column for state-based planning
        sql_query = text("""
            SELECT 
                COALESCE(season::TEXT, '') as season,
                COALESCE(village::TEXT, '') as village,
                COALESCE(hsp_code::TEXT, '') as hsp_code,
                COALESCE(crop::TEXT, '') as crop,
                COALESCE(state::TEXT, '') as state,
                COALESCE(NULLIF(net_acreage_area::TEXT, ''), '0') as net_acreage_area,
                COALESCE(NULLIF(packed_qty::TEXT, ''), '0') as packed_qty,
                COALESCE(NULLIF(sum_of_received_qty::TEXT, ''), '0') as sum_of_received_qty,
                COALESCE(NULLIF(amount_inr::TEXT, ''), '0') as amount_inr,
                COALESCE(NULLIF(productivity_of_packed_seed::TEXT, ''), '0') as productivity_of_packed_seed
            FROM operations.season_crop_inspection_final
            WHERE season IS NOT NULL
              AND village IS NOT NULL
              AND hsp_code IS NOT NULL
        """)
        
        # Execute query manually and build DataFrame to avoid type issues
        # Access rows as tuples to bypass SQLAlchemy type processing
        result = self.db.execute(sql_query)
        
        # Convert to list of dicts - all values come as strings, convert in Python
        data_list = []
        for row_tuple in result:
            try:
                # row_tuple is a Row object, convert to tuple for indexing
                row = tuple(row_tuple)
                data_list.append({
                    'season': str(row[0]) if row[0] else '',
                    'village': str(row[1]) if row[1] else '',
                    'hsp_code': str(row[2]) if row[2] else '',
                    'crop': str(row[3]) if row[3] else '',
                    'state': str(row[4]) if row[4] else '',
                    'net_acreage_area': round(self._safe_to_numeric(pd.Series([row[5]])).iloc[0] if row[5] else 0.0, 2),
                    'packed_qty': round(self._safe_to_numeric(pd.Series([row[6]])).iloc[0] if row[6] else 0.0, 2),
                    'sum_of_received_qty': round(self._safe_to_numeric(pd.Series([row[7]])).iloc[0] if row[7] else 0.0, 2),
                    'amount_inr': round(self._safe_to_numeric(pd.Series([row[8]])).iloc[0] if row[8] else 0.0, 2),
                    'productivity_of_packed_seed': round(self._safe_to_numeric(pd.Series([row[9]])).iloc[0] if row[9] else 0.0, 2)
                })
            except (ValueError, TypeError, IndexError) as ve:
                continue
        
        self.data = pd.DataFrame(data_list)
        
        # Rename columns to match expected format
        self.data = self.data.rename(columns={
            'season': 'Season',
            'village': 'Village',
            'hsp_code': 'HSP Code',
            'crop': 'Crop',
            'state': 'State',
            'net_acreage_area': 'Net Acreage Area',
            'packed_qty': 'Packed Qty',
            'sum_of_received_qty': 'Sum of Received Qty',
            'amount_inr': 'Amount_Inr',
            'productivity_of_packed_seed': 'Productivity of Packed Seed'
        })
        
        print(f"Data loaded from database. Shape: {self.data.shape}")
        
        # Now prepare the data using the same logic as CSV
        self.prepare_data()
        
        # Debug: Show top data with state column (after prepare_data which renames columns)
        if not self.data.empty:
            print("\n[DEBUG] Top 5 rows loaded from database (showing State column):")
            state_cols = ['State', 'Village', 'Variety', 'Crop', 'Season'] if 'State' in self.data.columns else ['Village', 'Variety', 'Crop', 'Season']
            # Only show columns that exist
            available_cols = [col for col in state_cols if col in self.data.columns]
            if available_cols:
                print(self.data[available_cols].head())
            print(f"\n[DEBUG] State column info:")
            print(f"  - State column exists: {'State' in self.data.columns}")
            if 'State' in self.data.columns:
                print(f"  - Non-null states: {self.data['State'].notna().sum()}")
                print(f"  - Unique states: {self.data['State'].dropna().unique()[:10]}")

    def prepare_data(self):
        """Prepare and clean the input data"""

        def extract_year(season_str):
            if pd.isna(season_str):
                return None
            season_str = str(season_str).upper()
            match = re.search(r'(20\d{2})-(\d{2})', season_str)
            if match:
                year1 = int(match.group(1))
                year2 = int(f"20{match.group(2)}")
                return f"{year1}-{year2}"
            match = re.search(r'(\d{2})-(\d{2})', season_str)
            if match:
                year1, year2 = int(match.group(1)), int(match.group(2))
                full_year1 = 2000 + year1
                full_year2 = 2000 + year2
                return f"{full_year1}-{full_year2}"
            return None

        # Standardize column names (strip)
        self.data.columns = self.data.columns.str.strip()

        # Season/year extraction (best-effort)
        if 'Season' in self.data.columns:
            self.data['Season_Year'] = self.data['Season'].apply(extract_year)
            self.data['Year'] = self.data['Season_Year'].apply(lambda x: int(x.split('-')[0]) if x else None)
        else:
            self.data['Season_Year'] = None
            self.data['Year'] = None

        # Normalize text columns if present
        for col in ['Village', 'Variety', 'Crop', 'HSP Code', 'Season', 'State']:
            if col in self.data.columns:
                self.data[col] = self.data[col].astype(str).str.strip()

        # Coerce numeric columns using the instance helper
        if 'Net Acreage Area' in self.data.columns:
            self.data['Net_harvest_Acres'] = self._safe_to_numeric(self.data['Net Acreage Area'])
        else:
            self.data['Net_harvest_Acres'] = self._safe_to_numeric(self.data.get('Net_harvest_Acres', pd.Series([])))

        if 'Packed Qty' in self.data.columns:
            self.data['Packed_output_kgs'] = self._safe_to_numeric(self.data['Packed Qty'])
        else:
            self.data['Packed_output_kgs'] = self._safe_to_numeric(self.data.get('Packed_output_kgs', pd.Series([])))

        if 'Sum of Received Qty' in self.data.columns:
            self.data['Received_Qty'] = self._safe_to_numeric(self.data['Sum of Received Qty'])
        else:
            self.data['Received_Qty'] = self._safe_to_numeric(self.data.get('Received_Qty', pd.Series([])))

        if 'Amount_Inr' in self.data.columns:
            self.data['Total_Cost'] = self._safe_to_numeric(self.data['Amount_Inr'])
        else:
            self.data['Total_Cost'] = self._safe_to_numeric(self.data.get('Total_Cost', pd.Series([])))

        if 'Productivity of Packed Seed' in self.data.columns:
            self.data['Productivity of Packed Seed'] = self._safe_to_numeric(self.data['Productivity of Packed Seed'])
        else:
            self.data['Productivity of Packed Seed'] = self._safe_to_numeric(pd.Series([], dtype=float))

        # Variety from HSP Code fallback
        if 'HSP Code' in self.data.columns:
            self.data['Variety'] = self.data['HSP Code'].fillna('Unknown').astype(str).str.strip()
        else:
            if 'Variety' not in self.data.columns:
                self.data['Variety'] = 'Unknown'
            else:
                self.data['Variety'] = self.data['Variety'].astype(str).str.strip()

        # Normalized helper columns
        self.data['village_norm'] = self.data['Village'].astype(str).str.strip().str.lower()
        self.data['variety_norm'] = self.data['Variety'].astype(str).str.strip().str.lower()

        # Drop rows missing mandatory fields
        self.data = self.data.dropna(subset=['Year', 'Village', 'Variety'])

        print(f"Data loaded successfully. Shape: {self.data.shape}")
        print(f"Available seasons: {sorted(self.data['Season_Year'].dropna().unique())}")
        print(f"Available years: {sorted(self.data['Year'].dropna().unique())}")
        print(f"Available villages: {self.data['Village'].nunique()}")
        print(self.data[['Sum of Received Qty', 'Received_Qty']].head())



    def determine_hybrid_type(self, village: str, variety: str) -> Tuple[str, int]:
        village_variety_data = self.data[
            (self.data['Village'].str.lower() == str(village).strip().lower()) &
            (self.data['Variety'].str.lower() == str(variety).strip().lower())
        ]
        years_available = int(village_variety_data['Year'].nunique()) if not village_variety_data.empty else 0
        hybrid_type = 'New' if years_available <= 3 else 'Old'
        return hybrid_type, years_available


    def calculate_avg_yield_old_hybrids(self, village: str, variety: str) -> float:
        village_variety_data = self.data[
            (self.data['Village'].str.lower() == str(village).strip().lower()) &
            (self.data['Variety'].str.lower() == str(variety).strip().lower())
        ].copy()

        if village_variety_data.empty:
            return 0.0

        yearly_data = village_variety_data.groupby('Year').agg({
            'Packed_output_kgs': 'sum',
            'Net_harvest_Acres': 'sum'
        }).reset_index()

        yearly_data = yearly_data.sort_values('Year', ascending=False).head(5)

        yearly_data['Packed_output_kgs'] = self._safe_to_numeric(yearly_data['Packed_output_kgs'])
        yearly_data['Net_harvest_Acres'] = self._safe_to_numeric(yearly_data['Net_harvest_Acres'])

        yearly_data['Yield_per_Acre'] = np.where(
            yearly_data['Net_harvest_Acres'] > 0,
            yearly_data['Packed_output_kgs'] / yearly_data['Net_harvest_Acres'],
            0.0
        )

        valid_yields = yearly_data[yearly_data['Yield_per_Acre'] > 0]['Yield_per_Acre']
        if valid_yields.empty:
            return 0.0

        return float(valid_yields.mean())



    def calculate_avg_yield_new_hybrids(self, village: str, variety: str, y0_year: int, debug=False) -> float:
        village_key = str(village).strip().lower()
        variety_key = str(variety).strip().lower()
        df = self.data

        mask = (
            (df['village_norm'] == village_key) &
            (df['variety_norm'] == variety_key) &
            (df['Year'] == y0_year)
        )
        matched = df.loc[mask]

        if matched.empty:
            mask = (
                (df['village_norm'] == village_key) &
                (df['variety_norm'] == variety_key) &
                (df['Year'].astype(str).str.contains(str(y0_year), na=False))
            )
            matched = df.loc[mask]

        if debug:
            print(f"[DEBUG] rows matched for ({village_key}, {variety_key}, {y0_year}): {len(matched)}")
            if len(matched) > 0:
                print(matched[['Year', 'Village', 'Variety', 'Packed_output_kgs', 'Net_harvest_Acres']].head(5))

        if matched.empty:
            return 0.0

        matched['Packed_output_kgs'] = self._safe_to_numeric(matched['Packed_output_kgs'])
        matched['Net_harvest_Acres'] = self._safe_to_numeric(matched['Net_harvest_Acres'])

        total_packed = matched['Packed_output_kgs'].sum(skipna=True)
        total_acres = matched['Net_harvest_Acres'].sum(skipna=True)

        if total_acres == 0 or total_packed == 0:
            return 0.0

        return float(total_packed / total_acres)

    def normalize_variety_to_us_code(self, variety: str) -> str:
        """
        Normalize variety/HSP code to USRH-code format
        Handles variations like 'US04', 'USRH-04', 'US-04', 'USRH04', 'USRH 04', etc.
        """
        if not variety:
            return ''
        variety = str(variety).strip().upper()
        import re
        
        # First try to match USRH pattern (USRH-04, USRH04, USRH 04, etc.)
        usrh_match = re.search(r'USRH[\s\-]?(\d+)', variety)
        if usrh_match:
            return f"USRH-{usrh_match.group(1).zfill(2)}"
        
        # Then try to match US pattern (US04, US 04, US-04, etc.)
        us_match = re.search(r'US[\s\-]?(\d+)', variety)
        if us_match:
            return f"USRH-{us_match.group(1).zfill(2)}"
        
        # If no pattern matches, return as-is (might already be in correct format)
        return variety
    
    def normalize_state_name(self, state: str) -> str:
        """
        Normalize state name for matching
        Handles variations in state names
        """
        if not state:
            return ''
        state = str(state).strip()
        # Map common variations
        state_mapping = {
            'chhattisgarh': 'Chattissgarh',
            'chattisgarh': 'Chattissgarh',
            'chattissgarh': 'Chattissgarh',
            'odisha': 'Odisha',
            'orissa': 'Odisha',
            'karnataka': 'Karnataka',
            'warangal': 'Telangana',  # Warangal is a district in Telangana
            'karimnagar': 'Telangana',  # Karimnagar is a district in Telangana
            'telangana': 'Telangana'
        }
        state_lower = state.lower()
        return state_mapping.get(state_lower, state)
    
    def set_targets_for_plan_year(self, plan_year: int):
        """
        Set the appropriate targets based on plan year
        """
        if plan_year == 2024:
            # Use 2024-2025 targets (from new image)
            self.state_variety_targets = self.state_variety_targets_2024.copy()
        elif plan_year == 2025:
            # Use 2025-2026 targets (existing targets)
            self.state_variety_targets = self.state_variety_targets_2025.copy()
        else:
            # Default to 2025 targets for other years
            self.state_variety_targets = self.state_variety_targets_2025.copy()
        
        # Convert targets from MT to Kgs
        self.state_variety_targets_kgs = {}
        for state, varieties in self.state_variety_targets.items():
            self.state_variety_targets_kgs[state] = {
                variety: target_mt * 1000 for variety, target_mt in varieties.items() if target_mt > 0
            }
    
    def get_target_for_state_variety(self, state: str, variety: str) -> float:
        """
        Get target production (in Kgs) for a state-variety combination
        Returns 0 if no target is defined
        """
        state = str(state).strip()
        us_code = self.normalize_variety_to_us_code(variety)
        
        if state in self.state_variety_targets_kgs:
            return self.state_variety_targets_kgs[state].get(us_code, 0.0)
        return 0.0

    def calculate_cost_per_kg(self, village: str, variety: str, y0_year: int, season: str = None, crop: str = None) -> float:
        """
        Calculate cost per kg for a village-variety combination
        Filters by village, variety, year, and optionally season and crop
        """
        filters = [
            (self.data['Village'].str.lower() == village.lower()),
            (self.data['Variety'].str.lower() == variety.lower()),
            (self.data['Year'] == y0_year)
        ]
        
        # Add season filter if provided
        if season:
            filters.append(self.data['Season'] == season)
        
        # Add crop filter if provided
        if crop:
            filters.append(self.data['Crop'] == crop)
        
        village_variety_data = self.data[np.logical_and.reduce(filters)]
        
        if village_variety_data.empty:
            return 0.0

        y0_received = self._safe_to_numeric(village_variety_data['Received_Qty']).sum()
        y0_total_cost = self._safe_to_numeric(village_variety_data['Total_Cost']).sum()

        if y0_received == 0 or y0_total_cost == 0:
            return 0.0

        return float(y0_total_cost / y0_received)



    def generate_village_summary(self, y0_year: int, y1_year: int) -> pd.DataFrame:
        summary_data = []

        for col in ['Village', 'Variety', 'Season', 'Crop', 'State']:
            if col in self.data.columns:
                self.data[col] = self.data[col].astype(str).str.strip()

        # Determine Y1 season dynamically at the start
        if y1_year == 2024:
            y1_season = 'RABI 24-25'
        elif y1_year == 2025:
            y1_season = 'RABI 25-26'
        else:
            y1_season = f'RABI {y1_year}-{str(y1_year + 1)[-2:]}'

        unique_villages = (
            self.data[self.data['Year'] == y0_year][['Village', 'Season', 'Crop', 'State']]
            .drop_duplicates()
        )

        for _, vrow in unique_villages.iterrows():
            village = vrow['Village']
            season = vrow['Season']
            crop = vrow['Crop']
            state = vrow.get('State', '') if 'State' in vrow else ''

            varieties = (
                self.data[
                    (self.data['Village'].str.lower() == village.lower()) &
                    (self.data['Season'] == season) &
                    (self.data['Crop'] == crop) &
                    (self.data['Year'] == y0_year)
                    ]['Variety'].drop_duplicates()
            )

            for variety in varieties:
                y0_data = self.data[
                    (self.data['Village'].str.lower() == village.lower()) &
                    (self.data['Variety'] == variety) &
                    (self.data['Season'] == season) &
                    (self.data['Crop'] == crop) &
                    (self.data['Year'] == y0_year)
                    ]
                if y0_data.empty:
                    continue

                y1_data = self.data[
                    (self.data['Village'].str.lower() == village.lower()) &
                    (self.data['Variety'] == variety) &
                    (self.data['Season'] == y1_season) &
                    (self.data['Crop'] == crop) &
                    (self.data['Year'] == y1_year)
                    ]

                # Safe numeric aggregation from Y0 data (grouped by village-variety-season-crop)
                y0_net_acres = float(self._safe_to_numeric(y0_data['Net_harvest_Acres']).sum() or 0.0)
                y0_packed_qty = float(self._safe_to_numeric(y0_data['Packed_output_kgs']).sum() or 0.0)
                y0_received_qty = float(self._safe_to_numeric(y0_data['Received_Qty']).sum() or 0.0)
                y0_total_cost = float(self._safe_to_numeric(y0_data['Total_Cost']).sum() or 0.0)
                
                # Calculate y0_avg_productivity directly from Y0 aggregated data (matching reference service)
                y0_avg_productivity = float((y0_packed_qty / y0_net_acres) if y0_net_acres > 0 else 0.0)
                
                # Calculate estimated_cost_per_kg directly from Y0 aggregated data (matching reference service)
                estimated_cost_per_kg = float((y0_total_cost / y0_received_qty) if y0_received_qty > 0 else 0.0)
                
                # Y1 data aggregation
                y1_packed_qty = float(self._safe_to_numeric(y1_data['Packed_output_kgs']).sum() or 0.0) if not y1_data.empty else 0.0
                y1_received_qty = float(self._safe_to_numeric(y1_data['Received_Qty']).sum() or 0.0) if not y1_data.empty else 0.0
                y1_net_acres = float(self._safe_to_numeric(y1_data['Net_harvest_Acres']).sum() or 0.0) if not y1_data.empty else 0.0
                y1_total_cost = float(self._safe_to_numeric(y1_data['Total_Cost']).sum() or 0.0) if not y1_data.empty else 0.0

                if 'Productivity of Packed Seed' in y1_data.columns and not y1_data.empty:
                    actual_productivity = float(self._safe_to_numeric(y1_data['Productivity of Packed Seed']).sum() or 0.0)
                else:
                    actual_productivity = 0.0

                # Determine hybrid type for classification only (not used for productivity calculation)
                hybrid_type, years_available = self.determine_hybrid_type(village, variety)
                
                # Calculate required metrics with explicit float conversion
                production_allocation = float(y0_avg_productivity * y0_net_acres)
                planned_production = float(y0_avg_productivity * y0_net_acres)
                # available_acres: use y1_net_acres if y1_data exists and y1_net_acres > 0, otherwise use y0_net_acres
                # For plans like 25-26 where y1 data is not available, use y0_net_acres (current net acres)
                # For plans like 24-25 where y1 data is available, use y1_net_acres
                # Always ensure available_acres is not zero when y0_net_acres > 0 - use y0_net_acres as fallback
                if y1_net_acres > 0:
                    # Y1 data exists and has net acres > 0, use Y1 net acres
                    available_acres = float(y1_net_acres)
                else:
                    # Y1 data not available or y1_net_acres is 0, use Y0 net acres as fallback
                    # This ensures available_acres is never zero when y0_net_acres > 0
                    available_acres = float(y0_net_acres)
                adjusted_production_allocation = float(y0_avg_productivity * available_acres)
                estimated_production_cost = float(estimated_cost_per_kg * adjusted_production_allocation)
                y1_productivity = float((y1_packed_qty / y1_net_acres) if y1_net_acres > 0 else 0.0)

                summary_data.append({
                    'village': village,
                    'variety': variety,
                    'season': y1_season,
                    'crop': crop,
                    'state': state,
                    'y0_avg_productivity': round(y0_avg_productivity, 2),
                    'y0_net_acres': round(y0_net_acres, 2),
                    'estimated_cost_per_kg': round(estimated_cost_per_kg, 2),
                    'Hybrid_Type': hybrid_type,
                    'Years_Available': years_available,
                    'y1_packed_quantity': round(y1_packed_qty, 2),
                    'y1_sum_received_qty': round(y1_received_qty, 2),
                    'y1_net_acres': round(y1_net_acres, 2),
                    'y1_amount': round(y1_total_cost, 2),
                    'y1_productivity': round(y1_productivity, 2),
                    'production_allocation': round(production_allocation, 2),
                    'planned_production': round(planned_production, 2),
                    'adjusted_production_allocation': round(adjusted_production_allocation, 2),
                    'estimated_production_cost': round(estimated_production_cost, 2),
                    'allocated_acres': 0.0,
                    'probable_cost': 0.0,
                    'is_planned_village': True,  # Mark as planned (has Y0 data)
                    'available_acres': round(available_acres, 2)
                })

        # Process new villages (Y1-only, not in Y0)
        # y1_season already determined at the start of the function
        
        # Get all Y1 villages
        y1_unique_villages = (
            self.data[
                (self.data['Year'] == y1_year) & 
                (self.data['Season'] == y1_season)
            ][['Village', 'Crop', 'State']]
            .drop_duplicates()
        )
        
        # Track which villages are already in summary (from Y0)
        existing_keys = set()
        for record in summary_data:
            key = f"{record['village'].lower()}_{record['variety'].lower()}_{record['crop'].lower()}"
            existing_keys.add(key)
        
        # Process Y1-only villages
        for _, vrow in y1_unique_villages.iterrows():
            village = vrow['Village']
            crop = vrow['Crop']
            state = vrow.get('State', '') if 'State' in vrow else ''
            
            # Get varieties for this village-crop in Y1
            y1_varieties = (
                self.data[
                    (self.data['Village'].str.lower() == village.lower()) &
                    (self.data['Season'] == y1_season) &
                    (self.data['Crop'] == crop) &
                    (self.data['Year'] == y1_year)
                ]['Variety'].drop_duplicates()
            )
            
            for variety in y1_varieties:
                # Check if this village-variety-crop already exists in summary
                key = f"{village.lower()}_{variety.lower()}_{crop.lower()}"
                if key in existing_keys:
                    continue  # Already processed from Y0
                
                # Get Y1 data for this village-variety-crop
                y1_data = self.data[
                    (self.data['Village'].str.lower() == village.lower()) &
                    (self.data['Variety'] == variety) &
                    (self.data['Season'] == y1_season) &
                    (self.data['Crop'] == crop) &
                    (self.data['Year'] == y1_year)
                ]
                
                if y1_data.empty:
                    continue
                
                # Calculate Y1 metrics with explicit float conversion
                y1_packed_qty = float(self._safe_to_numeric(y1_data['Packed_output_kgs']).sum() or 0.0)
                y1_received_qty = float(self._safe_to_numeric(y1_data['Received_Qty']).sum() or 0.0)
                y1_net_acres = float(self._safe_to_numeric(y1_data['Net_harvest_Acres']).sum() or 0.0)
                y1_total_cost = float(self._safe_to_numeric(y1_data['Total_Cost']).sum() or 0.0)
                y1_productivity = float((y1_packed_qty / y1_net_acres) if y1_net_acres > 0 else 0.0)
                
                if 'Productivity of Packed Seed' in y1_data.columns and not y1_data.empty:
                    actual_productivity = float(self._safe_to_numeric(y1_data['Productivity of Packed Seed']).sum() or 0.0)
                else:
                    actual_productivity = 0.0
                
                # Determine hybrid type
                hybrid_type, years_available = self.determine_hybrid_type(village, variety)
                
                # Add new village record (Y1-only, no Y0 data)
                summary_data.append({
                    'village': village,
                    'variety': variety,
                    'season': y1_season,
                    'crop': crop,
                    'state': state,
                    'y0_avg_productivity': 0.0,
                    'y0_net_acres': 0.0,
                    'estimated_cost_per_kg': 0.0,
                    'Hybrid_Type': hybrid_type,
                    'Years_Available': years_available,
                    'y1_packed_quantity': round(y1_packed_qty, 2),
                    'y1_sum_received_qty': round(y1_received_qty, 2),
                    'y1_net_acres': round(y1_net_acres, 2),
                    'y1_amount': round(y1_total_cost, 2),
                    'y1_productivity': round(y1_productivity, 2),
                    'production_allocation': 0.0,
                    'planned_production': 0.0,
                    'adjusted_production_allocation': 0.0,
                    'estimated_production_cost': 0.0,
                    'allocated_acres': 0.0,
                    'probable_cost': 0.0,
                    'is_planned_village': False,  # Mark as new village (Y1-only)
                    'available_acres': round(y1_net_acres, 2)
                })

        return pd.DataFrame(summary_data)


    def allocate_target_production_lp(self, village_summary: pd.DataFrame):
        """
        Allocate target production per state-variety using two-stage Linear Programming.
        Stage 1: Maximize yield for each state-variety
        Stage 2: Minimize cost while maintaining max yield from Stage 1
        Only considers villages within the same state for each variety.
        """
        village_summary = village_summary.reset_index(drop=True)
        
        # Debug: Check state column at start of allocation
        print(f"\n[DEBUG] At start of allocation:")
        print(f"  - State column exists: {'state' in village_summary.columns}")
        if 'state' in village_summary.columns:
            non_null = village_summary['state'].notna().sum()
            print(f"  - Non-null states: {non_null} out of {len(village_summary)}")
        
        # Initialize allocation columns
        village_summary['allocated_acres'] = 0.0
        village_summary['probable_cost'] = 0.0
        village_summary['adjusted_production_allocation'] = 0.0
        village_summary['estimated_production_cost'] = 0.0
        
        # Ensure state column exists - if missing, try to get from 'State' column
        if 'state' not in village_summary.columns:
            if 'State' in village_summary.columns:
                village_summary['state'] = village_summary['State'].astype(str).fillna('')
            else:
                village_summary['state'] = ''
                print(f"  - WARNING: No 'state' or 'State' column found, creating empty state column")
        
        # Group by state and variety, then allocate separately
        state_variety_stats = []
        
        # Get unique state-variety combinations from targets
        for state, variety_targets in self.state_variety_targets_kgs.items():
            for variety_us_code, target_kgs in variety_targets.items():
                # Normalize state name for matching
                normalized_target_state = self.normalize_state_name(state)
                
                # Find villages matching this state and variety (normalize state and variety to match)
                # Normalize states in village_summary for comparison
                village_summary_normalized = village_summary.copy()
                village_summary_normalized['state_normalized'] = village_summary_normalized['state'].apply(
                    lambda x: self.normalize_state_name(str(x)) if pd.notna(x) else ''
                )
                
                state_villages = village_summary_normalized[
                    (village_summary_normalized['state_normalized'].str.strip().str.lower() == normalized_target_state.strip().lower())
                ].copy()
                
                # Remove the temporary normalized column
                if 'state_normalized' in state_villages.columns:
                    state_villages = state_villages.drop(columns=['state_normalized'])
                
                if state_villages.empty:
                    state_variety_stats.append({
                        'state': state,
                        'variety': variety_us_code,
                        'target_kgs': target_kgs,
                        'target_mt': target_kgs / 1000,
                        'allocated_kgs': 0.0,
                        'allocated_mt': 0.0,
                        'allocated_acres': 0.0,
                        'total_cost': 0.0,
                        'num_villages': 0,
                        'status': 'No villages found in state'
                    })
                    continue
                
                # Filter villages that match the variety (normalize HSP codes to USRH-codes)
                matching_villages = []
                for idx, row in state_villages.iterrows():
                    village_variety = str(row.get('variety', ''))
                    normalized_variety = self.normalize_variety_to_us_code(village_variety)
                    if normalized_variety == variety_us_code:
                        matching_villages.append(idx)
                
                if not matching_villages:
                    state_variety_stats.append({
                        'state': state,
                        'variety': variety_us_code,
                        'target_kgs': target_kgs,
                        'target_mt': target_kgs / 1000,
                        'allocated_kgs': 0.0,
                        'allocated_mt': 0.0,
                        'allocated_acres': 0.0,
                        'total_cost': 0.0,
                        'num_villages': 0,
                        'status': f'No matching variety found (looking for {variety_us_code})'
                    })
                    continue
                
                # Get villages for this state-variety combination
                state_variety_villages = village_summary.loc[matching_villages].copy()
                
                # Filter planned villages with positive productivity and available acres for LP
                planned_mask = (
                    (state_variety_villages['is_planned_village'] == True) &
                    (state_variety_villages['y0_avg_productivity'] > 0) &
                    (state_variety_villages['available_acres'] > 0)
                )
                planned_villages_for_lp = state_variety_villages[planned_mask].copy()
                
                # Store original indices for updating village_summary later
                original_indices = planned_villages_for_lp.index.tolist()
                
                if planned_villages_for_lp.empty:
                    state_variety_stats.append({
                        'state': state,
                        'variety': variety_us_code,
                        'target_kgs': target_kgs,
                        'target_mt': target_kgs / 1000,
                        'allocated_kgs': 0.0,
                        'allocated_mt': 0.0,
                        'allocated_acres': 0.0,
                        'total_cost': 0.0,
                        'num_villages': len(state_variety_villages),
                        'status': 'No planned villages with positive productivity'
                    })
                    continue
                
                # Reset index for LP processing
                planned_villages_for_lp = planned_villages_for_lp.reset_index(drop=True)
                
                # Run LP only on planned villages for this state-variety
                keys = planned_villages_for_lp.index
                y = {i: planned_villages_for_lp.loc[i, 'y0_avg_productivity'] for i in keys}
                A = {i: planned_villages_for_lp.loc[i, 'available_acres'] for i in keys}
                c = {i: planned_villages_for_lp.loc[i, 'estimated_cost_per_kg'] for i in keys}

                max_possible_prod = sum(y[i] * A[i] for i in keys)
                target = min(target_kgs, max_possible_prod)
                
                print(f"\nState: {state}, Variety: {variety_us_code}")
                print(f"  Target: {target:,.0f} Kg ({target/1000:.2f} MT)")
                print(f"  Max possible: {max_possible_prod:,.0f} Kg ({max_possible_prod/1000:.2f} MT)")
                print(f"  Villages available: {len(planned_villages_for_lp)}")

                # Stage 1: Maximize yield
                x = {i: LpVariable(f"x_{i}_{state}_{variety_us_code}", lowBound=0, upBound=A[i]) for i in keys}
                prob1 = LpProblem(f"Maximize_Yield_{state}_{variety_us_code}", LpMaximize)
                prob1 += lpSum(y[i] * x[i] for i in keys)
                prob1 += lpSum(y[i] * x[i] for i in keys) >= target
                prob1.solve()
                if LpStatus[prob1.status] != 'Optimal':
                    print(f"  Stage 1 LP did not find an optimal solution: {LpStatus[prob1.status]}")
                    state_variety_stats.append({
                        'state': state,
                        'variety': variety_us_code,
                        'target_kgs': target_kgs,
                        'target_mt': target_kgs / 1000,
                        'allocated_kgs': 0.0,
                        'allocated_mt': 0.0,
                        'allocated_acres': 0.0,
                        'total_cost': 0.0,
                        'num_villages': len(planned_villages_for_lp),
                        'status': f'LP Stage 1 failed: {LpStatus[prob1.status]}'
                    })
                    continue

                max_yield = float(sum(y[i] * (x[i].varValue if x[i].varValue is not None else 0) for i in keys))

                # Stage 2: Minimize cost fixing max yield
                x2 = {i: LpVariable(f"x2_{i}_{state}_{variety_us_code}", lowBound=0, upBound=A[i]) for i in keys}
                prob2 = LpProblem(f"Minimize_Cost_{state}_{variety_us_code}", LpMinimize)
                prob2 += lpSum(y[i] * x2[i] * c[i] for i in keys)
                prob2 += lpSum(y[i] * x2[i] for i in keys) == max_yield
                prob2.solve()
                if LpStatus[prob2.status] != 'Optimal':
                    print(f"  Stage 2 LP did not find an optimal solution: {LpStatus[prob2.status]}")
                    state_variety_stats.append({
                        'state': state,
                        'variety': variety_us_code,
                        'target_kgs': target_kgs,
                        'target_mt': target_kgs / 1000,
                        'allocated_kgs': 0.0,
                        'allocated_mt': 0.0,
                        'allocated_acres': 0.0,
                        'total_cost': 0.0,
                        'num_villages': len(planned_villages_for_lp),
                        'status': f'LP Stage 2 failed: {LpStatus[prob2.status]}'
                    })
                    continue

                total_cost = 0.0
                total_acres = 0.0
                allocated = 0.0

                for i in keys:
                    acres_allocated = float(x2[i].varValue if x2[i].varValue is not None else 0)
                    
                    # Calculate production and cost correctly with explicit float conversion
                    production = float(acres_allocated * y[i])  # allocated_acres * y0_avg_productivity
                    
                    # Get original index in village_summary
                    orig_idx = original_indices[i]
                    
                    # Update the LP-allocated values in village_summary
                    village_summary.loc[orig_idx, 'allocated_acres'] = round(float(acres_allocated), 2)
                    village_summary.loc[orig_idx, 'adjusted_production_allocation'] = round(production, 2)
                    
                    # Recalculate estimated_production_cost based on adjusted_production_allocation
                    estimated_production_cost = float(c[i] * production)
                    village_summary.loc[orig_idx, 'estimated_production_cost'] = round(estimated_production_cost, 2)
                    village_summary.loc[orig_idx, 'probable_cost'] = round(estimated_production_cost, 2)
                    
                    allocated += production
                    total_cost += estimated_production_cost
                    total_acres += acres_allocated
                
                # Record statistics for this state-variety
                state_variety_stats.append({
                    'state': state,
                    'variety': variety_us_code,
                    'target_kgs': target_kgs,
                    'target_mt': target_kgs / 1000,
                    'allocated_kgs': round(allocated, 2),
                    'allocated_mt': round(allocated / 1000, 2),
                    'allocated_acres': round(total_acres, 2),
                    'total_cost': round(total_cost, 2),
                    'num_villages': len(planned_villages_for_lp),
                    'status': 'Allocated' if allocated > 0 else 'No allocation'
                })
                
                print(f"  Allocated: {allocated:,.0f} Kg ({allocated/1000:.2f} MT), Cost: Rs {total_cost:,.0f}, Acres: {total_acres:.2f}")
        
        # Store statistics for later use
        self.state_variety_statistics = pd.DataFrame(state_variety_stats)
        
        # Ensure new villages (Y1-only) have zero allocations
        new_villages_mask = village_summary['is_planned_village'] == False
        village_summary.loc[new_villages_mask, 'allocated_acres'] = 0.0
        village_summary.loc[new_villages_mask, 'probable_cost'] = 0.0
        village_summary.loc[new_villages_mask, 'adjusted_production_allocation'] = 0.0
        village_summary.loc[new_villages_mask, 'estimated_production_cost'] = 0.0
        
        # Debug: Check state column after allocation
        if 'state' in village_summary.columns:
            non_null_states = village_summary['state'].notna().sum()
            print(f"\n[DEBUG] After allocation:")
            print(f"  - State column exists: True")
            print(f"  - Non-null states: {non_null_states} out of {len(village_summary)}")
            if non_null_states == 0:
                print(f"  - WARNING: All state values are null after allocation!")
        
        # Return ALL villages (planned + new)
        return village_summary.copy().reset_index(drop=True)

    def _safe_int_conversion(self, value) -> Optional[int]:
        """Safely convert string ID to integer, return None if conversion fails"""
        if value is None:
            return None
        try:
            # Try to convert string to int if it's numeric
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
            elif isinstance(value, int):
                return value
            else:
                return None
        except (ValueError, TypeError):
            return None

    def verify_saved_plan(self, plan_version: str = None, plan_year: int = None) -> Dict:
        """
        Verify that planning data was saved to the database
        
        Args:
            plan_version: Optional version string to filter by
            plan_year: Optional year to filter by
            
        Returns:
            Dictionary with verification results
        """
        try:
            query = self.db.query(SupplyChainPlanning)
            
            if plan_version:
                query = query.filter(SupplyChainPlanning.plan_revision_version == plan_version)
            
            if plan_year:
                query = query.filter(SupplyChainPlanning.season_id == plan_year)
            
            total_count = query.count()
            
            # Get latest version if not specified
            if not plan_version:
                latest = self.db.query(
                    SupplyChainPlanning.plan_revision_version,
                    func.count(SupplyChainPlanning.id).label('count')
                ).group_by(
                    SupplyChainPlanning.plan_revision_version
                ).order_by(
                    func.max(SupplyChainPlanning.created_at).desc()
                ).first()
                
                if latest:
                    plan_version = latest.plan_revision_version
                    total_count = latest.count
            
            result = {
                'success': total_count > 0,
                'total_records': total_count,
                'plan_version': plan_version,
                'plan_year': plan_year
            }
            
            if total_count > 0:
                # Get some sample data
                sample = query.limit(5).all()
                result['sample_records'] = [
                    {
                        'village': r.village,
                        'variety': r.variety,
                        'crop': r.crop,
                        'season': r.season
                    }
                    for r in sample
                ]
            
            return result
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'total_records': 0
            }

    def save_plan_to_database(self, plan_data: pd.DataFrame, plan_year: int, plan_version: str = None) -> bool:
        """
        Save the generated plan to the supply_chain_planning table
        Equivalent to SupplyChainPlanningService.save_plan_to_database
        
        Args:
            plan_data: DataFrame containing the planning results
            plan_year: The planning year
            plan_version: Version string for the plan (defaults to LP version)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if plan_data.empty:
                logger.warning("No plan data to save")
                return False
            
            if not plan_version:
                plan_version = f"v3.0-Min-Max"
            
            # Get mappings for IDs
            # Create multiple lookup strategies for crops
            crop_name_to_id = {}
            for crop in self.db.query(CropRecord).all():
                crop_name_lower = crop.crop_name.strip().lower() if crop.crop_name else ''
                if crop_name_lower:
                    crop_name_to_id[crop_name_lower] = str(crop.crop_id) if crop.crop_id else None
                    # Also add without extra spaces
                    crop_name_to_id[crop_name_lower.replace(' ', ' ')] = str(crop.crop_id) if crop.crop_id else None
            
            # Create multiple lookup strategies for varieties
            variety_name_to_id = {}
            for variety in self.db.query(VarietyRecord).all():
                variety_name_lower = variety.variety_name.strip().lower() if variety.variety_name else ''
                if variety_name_lower:
                    variety_name_to_id[variety_name_lower] = str(variety.variety_id) if variety.variety_id else None
                    # Also add normalized version
                    normalized = self.normalize_variety_to_us_code(variety.variety_name).strip().lower()
                    if normalized and normalized != variety_name_lower:
                        variety_name_to_id[normalized] = str(variety.variety_id) if variety.variety_id else None
                    # Also add without dashes/spaces
                    no_dash = variety_name_lower.replace('-', '').replace(' ', '')
                    if no_dash and no_dash != variety_name_lower:
                        variety_name_to_id[no_dash] = str(variety.variety_id) if variety.variety_id else None
            
            # Create mapping for seasons (season_name to season_id)
            season_name_to_id = {}
            for season in self.db.query(SeasonRecord).all():
                season_name_lower = season.season_name.strip().lower() if season.season_name else ''
                if season_name_lower:
                    season_name_to_id[season_name_lower] = str(season.season_id) if season.season_id else None
                # Also add season_id as key (in case season_name is not set but season_id is used as name)
                if season.season_id:
                    season_id_lower = str(season.season_id).strip().lower()
                    if season_id_lower:
                        season_name_to_id[season_id_lower] = str(season.season_id)
            
            # Debug: Print sample mappings
            print(f"\n[DEBUG] Crop mappings sample (first 5):")
            for i, (name, cid) in enumerate(list(crop_name_to_id.items())[:5]):
                print(f"  '{name}' -> '{cid}'")
            print(f"\n[DEBUG] Variety mappings sample (first 5):")
            for i, (name, vid) in enumerate(list(variety_name_to_id.items())[:5]):
                print(f"  '{name}' -> '{vid}'")
            print(f"\n[DEBUG] Season mappings sample (first 5):")
            for i, (name, sid) in enumerate(list(season_name_to_id.items())[:5]):
                print(f"  '{name}' -> '{sid}'")
            
            # Create mappings for location data (ID, mandal, district)
            location_name_to_id = {}
            location_name_to_mandal = {}
            location_name_to_mandal_id = {}
            location_name_to_district = {}
            location_name_to_district_id = {}
            
            for loc in self.db.query(LocationRecord).all():
                village_key = loc.village.strip().lower() if loc.village else ''
                if village_key:
                    location_name_to_id[village_key] = str(loc.location_id) if loc.location_id else None
                    location_name_to_mandal[village_key] = str(loc.mandal) if loc.mandal else ''
                    location_name_to_mandal_id[village_key] = loc.mandal_id if loc.mandal_id is not None else None
                    location_name_to_district[village_key] = str(loc.district) if loc.district else ''
                    location_name_to_district_id[village_key] = loc.district_id if loc.district_id is not None else None
            
            # Deduplicate plan_data before processing to avoid duplicate inserts
            # Use key columns to identify duplicates
            key_columns = ['village', 'variety', 'crop', 'season', 'state']
            available_key_cols = [col for col in key_columns if col in plan_data.columns]
            if available_key_cols:
                initial_count = len(plan_data)
                plan_data = plan_data.drop_duplicates(subset=available_key_cols, keep='first')
                if len(plan_data) < initial_count:
                    logger.warning(f"Removed {initial_count - len(plan_data)} duplicate rows from plan_data before saving")
            
            # Check for existing records in database to avoid duplicates
            existing_records_query = self.db.query(SupplyChainPlanning).filter(
                SupplyChainPlanning.plan_revision_version == plan_version
            )
            existing_records = {
                (str(r.village or '').strip().upper(), 
                 str(r.variety or '').strip().upper(),
                 str(r.crop or '').strip().upper(),
                 str(r.season or '').strip(),
                 str(r.state or '').strip())
                for r in existing_records_query.all()
            }
            
            # Prepare records for insertion
            insert_records = []
            skipped_existing = 0
            for _, record in plan_data.iterrows():
                # Map names to IDs
                crop_name = str(record.get('crop', '')).strip().lower()
                variety_name_raw = str(record.get('variety', '')).strip()
                # Normalize variety name to match database format (e.g., USRH-04)
                variety_name_normalized = self.normalize_variety_to_us_code(variety_name_raw)
                variety_name = variety_name_normalized.strip().lower()
                village_name = str(record.get('village', '')).strip().lower()
                
                # Try multiple lookup strategies for variety
                variety_id_str = variety_name_to_id.get(variety_name, None)
                if variety_id_str is None:
                    # Try original variety name
                    variety_id_str = variety_name_to_id.get(variety_name_raw.strip().lower(), None)
                if variety_id_str is None:
                    # Try without normalization
                    variety_id_str = variety_name_to_id.get(variety_name_raw.strip().lower().replace('-', '').replace(' ', ''), None)
                if variety_id_str is None:
                    # Try uppercase version
                    variety_id_str = variety_name_to_id.get(variety_name_raw.strip().upper().lower(), None)
                
                # Try multiple lookup strategies for crop
                crop_id_str = crop_name_to_id.get(crop_name, None)
                if crop_id_str is None:
                    # Try with different spacing
                    crop_id_str = crop_name_to_id.get(crop_name.replace(' ', ' '), None)
                if crop_id_str is None:
                    # Try original case
                    crop_name_orig = str(record.get('crop', '')).strip()
                    crop_id_str = crop_name_to_id.get(crop_name_orig.lower(), None)
                
                location_id_str = location_name_to_id.get(village_name, None)
                
                # Get season_id from SeasonRecord mapping (before debug print)
                season_str = str(record.get('season', ''))
                season_name_lower = season_str.strip().lower()
                season_id_str = season_name_to_id.get(season_name_lower, None)
                # If not found, try with different variations
                if season_id_str is None:
                    # Try with extra spaces removed
                    season_id_str = season_name_to_id.get(season_name_lower.replace('  ', ' '), None)
                if season_id_str is None:
                    # Try original case
                    season_id_str = season_name_to_id.get(season_str.strip().lower(), None)
                # If still not found, fallback to extracting year (for backward compatibility)
                if season_id_str is None:
                    season_id = self.extract_year_from_season(season_str)
                    if season_id is None:
                        season_id = plan_year
                    season_id_str = str(season_id)
                else:
                    season_id_str = str(season_id_str)
                
                # Debug logging for first few records
                if len(insert_records) < 3:
                    print(f"\n[DEBUG] Record {len(insert_records) + 1} ID Lookup:")
                    print(f"  crop_name: '{crop_name}' -> crop_id: {crop_id_str}")
                    print(f"  variety_name (normalized): '{variety_name}' -> variety_id: {variety_id_str}")
                    print(f"  variety_name (raw): '{variety_name_raw}'")
                    print(f"  village_name: '{village_name}' -> location_id: {location_id_str}")
                    print(f"  season_name: '{season_str}' -> season_id: {season_id_str}")
                    if crop_id_str is None:
                        print(f"  [WARNING] Crop '{crop_name}' not found in database. Available crops: {list(crop_name_to_id.keys())[:10]}")
                    if variety_id_str is None:
                        print(f"  [WARNING] Variety '{variety_name}' not found. Available varieties (sample): {list(variety_name_to_id.keys())[:10]}")
                    if season_id_str is None or season_id_str == str(plan_year):
                        print(f"  [WARNING] Season '{season_str}' not found in database. Available seasons (sample): {list(season_name_to_id.keys())[:10]}")
                
                # crop_id and variety_id are Strings in database, not integers
                crop_id = str(crop_id_str) if crop_id_str else None
                variety_id = str(variety_id_str) if variety_id_str else None
                # location_id is String in database, keep as string
                location_id = str(location_id_str) if location_id_str else None
                
                # Get mandal and district from location mapping
                mandal = location_name_to_mandal.get(village_name, '')
                district = location_name_to_district.get(village_name, '')
                # location field - use village name or location name from LocationRecord
                location_name = str(record.get('village', ''))  # Default to village name
                
                # Get hybrid from Hybrid_Type column
                hybrid_type = str(record.get('Hybrid_Type', '')) or str(record.get('hybrid_type', '')) or ''
                
                # Get state value - handle both 'state' and 'State' column names
                state_value = str(record.get('state', '')) or str(record.get('State', '')) or ''
                if not state_value or state_value == 'nan' or state_value.lower() == 'none':
                    state_value = ''
                
                # Check if this record already exists in database
                record_key = (
                    str(record.get('village', '')).strip().upper(),
                    str(record.get('variety', '')).strip().upper(),
                    str(record.get('crop', '')).strip().upper(),
                    season_str.strip(),
                    state_value.strip()
                )
                if record_key in existing_records:
                    skipped_existing += 1
                    continue
                
                # Map DataFrame columns to database fields
                # Match the field structure from SupplyChainPlanningService.save_plan_to_database
                insert_record = {
                    'plan_revision_version': plan_version,
                    'season': season_str,
                    'season_id': season_id_str,
                    'crop': str(record.get('crop', '')),
                    'crop_id': crop_id,  # String type
                    'variety': str(record.get('variety', '')),
                    'variety_id': variety_id,  # String type
                    'village': str(record.get('village', '')),
                    'location_id': location_id,
                    'grower': '',  # Not available in current data
                    'state': state_value,  # Add state field with proper handling
                    'location': location_name,  # Add location field (village name)
                    'hybrid': hybrid_type,  # Add hybrid field
                    'mandal': mandal,  # Add mandal field
                    'district': district,  # Add district field
                    'net_acres_current': float(record.get('y0_net_acres', 0) or 0),
                    'productivity': float(record.get('y0_avg_productivity', 0) or 0),
                    'actual_productivity': float(record.get('y1_productivity', 0) or 0),
                    'production_allocation': float(record.get('production_allocation', 0) or 0),
                    'actual_net_acres': float(record.get('available_acres', 0) or 0),  # Save available_acres (y1 if available, else y0) to actual_net_acres
                    'adjusted_production_allocation': float(record.get('adjusted_production_allocation', 0) or 0),
                    'estimated_cost_per_kg': float(record.get('estimated_cost_per_kg', 0) or 0),
                    'estimated_production_cost': float(record.get('estimated_production_cost', 0) or 0),
                    'actual_received_qty': float(record.get('y1_sum_received_qty', 0) or 0),
                    'actual_amount': float(record.get('y1_amount', 0) or 0),
                    'actual_packaged_qty': float(record.get('y1_packed_quantity', 0) or 0),
                    'category_id': 100007,  # Supply chain planning category
                    'created_at': datetime.utcnow(),
                    'updated_at': datetime.utcnow()
                }
                
                insert_records.append(insert_record)
            
            # Debug: Show sample records being saved
            if insert_records:
                print(f"\n[DEBUG] Sample of {min(3, len(insert_records))} records being saved to database:")
                for i, rec in enumerate(insert_records[:3]):
                    print(f"  Record {i+1}: village={rec.get('village', 'N/A')}, state={rec.get('state', 'N/A')}, variety={rec.get('variety', 'N/A')}")
            if skipped_existing > 0:
                logger.info(f"Skipped {skipped_existing} records that already exist in database")
            
            # Insert records individually to handle duplicates gracefully
            if insert_records:
                saved_count = 0
                skipped_count = 0
                
                for insert_record in insert_records:
                    try:
                        # Create a new SupplyChainPlanning object
                        planning_record = SupplyChainPlanning(**insert_record)
                        self.db.add(planning_record)
                        self.db.commit()
                        saved_count += 1
                    except IntegrityError:
                        # Skip duplicate records and continue with others
                        self.db.rollback()
                        skipped_count += 1
                        continue
                    except Exception as e:
                        # Log other errors but continue
                        logger.warning(f"Error inserting record: {str(e)}")
                        self.db.rollback()
                        skipped_count += 1
                        continue
                
                logger.info(f"Successfully saved {saved_count} supply chain planning records")
                if skipped_count > 0:
                    logger.info(f"  Skipped {skipped_count} duplicate or invalid records")
                logger.info(f"  Plan Version: {plan_version}")
                logger.info(f"  Plan Year: {plan_year}")
                
                # Verify the save by querying the database
                verified_count = self.db.query(SupplyChainPlanning).filter(
                    SupplyChainPlanning.plan_revision_version == plan_version
                ).count()
                
                if verified_count > 0:
                    logger.info(f"Verification: Found {verified_count} records in database with version '{plan_version}'")
                else:
                    logger.warning("Could not verify records in database (this might be normal if using a different session)")
                
                return saved_count > 0
            else:
                logger.warning("No records to insert")
                return False
            
        except Exception as e:
            logger.error(f"Error saving plan to database: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self.db.rollback()
            return False

    def generate_plan(self, plan_year: int):
        # Set targets based on plan year ONLY if not already set from UI
        if not hasattr(self, 'targets_set_from_ui') or not self.targets_set_from_ui:
            self.set_targets_for_plan_year(plan_year)
        
        y0 = plan_year - 1
        y1 = plan_year

        print(f"\nGenerating plan for {plan_year} (Y0: {y0}, Y+1: {y1})")
        if self.data[self.data['Year'] == y0].empty:
            print(f"No data available for year {y0}")
            return pd.DataFrame()
        village_summary = self.generate_village_summary(y0, y1)
        if village_summary.empty:
            print(f"No valid data for villages in year {y0}")
            return pd.DataFrame()

        print(f"Villages in plan: {len(village_summary)}")
        print(f"Total potential production: {village_summary['adjusted_production_allocation'].sum():,.0f} Kg")

        final_plan = self.allocate_target_production_lp(village_summary)
        
        # Sort by is_planned_village descending, then by production_allocation descending, then by village
        # This matches supply_chain_planning_service.py sorting
        if 'is_planned_village' in final_plan.columns:
            final_plan = final_plan.sort_values(
                by=['is_planned_village', 'production_allocation', 'village'],
                ascending=[False, False, True]
            ).reset_index(drop=True)

        # Count planned vs new villages
        planned_count = final_plan['is_planned_village'].sum() if 'is_planned_village' in final_plan.columns else 0
        new_count = len(final_plan) - planned_count

        # Generate and display statistics
        self.print_state_variety_statistics(plan_year)
        
        print(f"\nPlan Summary:")
        total_target = sum(sum(varieties.values()) for varieties in self.state_variety_targets_kgs.values())
        print(f"- Total Target Production (from manual): {total_target:,.0f} Kg ({total_target / 1_000_000:.2f} MT)")
        print(f"- Allocated Production: {final_plan['adjusted_production_allocation'].sum():,.0f} Kg ({final_plan['adjusted_production_allocation'].sum() / 1_000_000:.2f} MT)")
        print(f"- Total Probable Cost: Rs {final_plan['probable_cost'].sum():,.0f}")
        print(f"- Total Allocated Acres: {final_plan['allocated_acres'].sum():,.2f}")
        print(f"- Total Villages: {final_plan['village'].nunique()}")
        print(f"  - Planned Villages: {planned_count}")
        print(f"  - New Villages (Y1-only): {new_count}")
        print(f"- Varieties Included: {final_plan['variety'].nunique()}")
        print(f"- Crops Included: {final_plan['crop'].nunique()}")
        if 'state' in final_plan.columns:
            print(f"- States Included: {final_plan['state'].nunique()}")

        # Reorder columns to match supply_chain_planning_service.py output
        column_order = [
            'state', 'village', 'variety', 'crop', 'season',
            'Hybrid_Type', 'Years_Available',
            'y0_net_acres', 'y0_avg_productivity',
            'allocated_acres', 'probable_cost',
            'y1_net_acres', 'y1_sum_received_qty', 'y1_packed_quantity',
            'y1_productivity', 'y1_amount',
            'production_allocation', 'planned_production',
            'adjusted_production_allocation',
            'estimated_cost_per_kg', 'estimated_production_cost',
            'is_planned_village', 'available_acres'
        ]
        
        # Keep only columns that exist in the dataframe
        existing_columns = [col for col in column_order if col in final_plan.columns]
        final_plan_ordered = final_plan[existing_columns].copy()
        
        # Debug: Check state column before saving
        print(f"\n[DEBUG] Before saving to database:")
        print(f"  - State column in final_plan: {'state' in final_plan.columns}")
        print(f"  - State column in final_plan_ordered: {'state' in final_plan_ordered.columns}")
        if 'state' in final_plan_ordered.columns:
            non_null_states = final_plan_ordered['state'].notna().sum()
            print(f"  - Non-null states in final_plan_ordered: {non_null_states} out of {len(final_plan_ordered)}")
            if non_null_states > 0:
                print(f"  - Sample state values: {final_plan_ordered['state'].dropna().head(5).tolist()}")
            else:
                print(f"  - WARNING: All state values are null!")
                print(f"  - Sample rows with state: {final_plan_ordered[['village', 'state', 'variety']].head(5)}")
        
        # Convert village names to uppercase
        if 'village' in final_plan_ordered.columns:
            final_plan_ordered['village'] = final_plan_ordered['village'].astype(str).str.upper()
        
        # Save the plan to database
        plan_version = f"v3.0-Min-Max"
        logger.info(f"\nSaving plan to database...")
        logger.info(f"  Plan Version: {plan_version}")
        logger.info(f"  Plan Year: {plan_year}")
        logger.info(f"  Records to save: {len(final_plan_ordered)}")
        
        save_success = self.save_plan_to_database(final_plan_ordered, plan_year, plan_version)
        
        if save_success:
            logger.info(f"Plan successfully saved to database!")
        else:
            logger.error(f" ERROR: Failed to save plan to database!")
            logger.error(f"  Please check the error messages above for details.")
        
        return final_plan_ordered

    def print_state_variety_statistics(self, plan_year: int):
        """
        Print detailed statistics per state-variety combination
        """
        if not hasattr(self, 'state_variety_statistics') or self.state_variety_statistics.empty:
            print("\nNo state-variety statistics available")
            return
        
        print("\n" + "=" * 100)
        print(f"STATE-VARIETY ALLOCATION STATISTICS (Plan Year: {plan_year})")
        print("=" * 100)
        
        # Group by state for better display
        for state in sorted(self.state_variety_statistics['state'].unique()):
            state_data = self.state_variety_statistics[self.state_variety_statistics['state'] == state]
            if state_data.empty:
                continue
            
            print(f"\n{state.upper()}:")
            print("-" * 100)
            print(f"{'Variety':<15} {'Target (MT)':<15} {'Allocated (MT)':<18} {'% Achieved':<12} {'Acres':<12} {'Cost (Rs)':<15} {'Status':<20}")
            print("-" * 100)
            
            state_total_target = 0
            state_total_allocated = 0
            state_total_cost = 0
            state_total_acres = 0
            
            for _, row in state_data.iterrows():
                target_mt = row['target_mt']
                allocated_mt = row['allocated_mt']
                pct_achieved = (allocated_mt / target_mt * 100) if target_mt > 0 else 0.0
                
                print(f"{row['variety']:<15} {target_mt:>12.2f} {allocated_mt:>15.2f} {pct_achieved:>10.1f}% "
                      f"{row['allocated_acres']:>10.2f} {row['total_cost']:>13,.0f} {row['status']:<20}")
                
                state_total_target += target_mt
                state_total_allocated += allocated_mt
                state_total_cost += row['total_cost']
                state_total_acres += row['allocated_acres']
            
            state_pct = (state_total_allocated / state_total_target * 100) if state_total_target > 0 else 0.0
            print("-" * 100)
            print(f"{'TOTAL':<15} {state_total_target:>12.2f} {state_total_allocated:>15.2f} {state_pct:>10.1f}% "
                  f"{state_total_acres:>10.2f} {state_total_cost:>13,.0f}")
        
        # Overall summary
        print("\n" + "=" * 100)
        print("OVERALL SUMMARY")
        print("=" * 100)
        total_target = self.state_variety_statistics['target_mt'].sum()
        total_allocated = self.state_variety_statistics['allocated_mt'].sum()
        total_cost = self.state_variety_statistics['total_cost'].sum()
        total_acres = self.state_variety_statistics['allocated_acres'].sum()
        overall_pct = (total_allocated / total_target * 100) if total_target > 0 else 0.0
        
        print(f"Total Target Production: {total_target:,.2f} MT ({total_target * 1000:,.0f} Kg)")
        print(f"Total Allocated Production: {total_allocated:,.2f} MT ({total_allocated * 1000:,.0f} Kg)")
        print(f"Overall Achievement: {overall_pct:.1f}%")
        print(f"Total Allocated Acres: {total_acres:,.2f}")
        print(f"Total Cost: Rs {total_cost:,.0f}")
        print("=" * 100)
        
        # Save statistics to CSV (skip if output_folder is temp directory)
        if self.output_folder and self.output_folder != tempfile.gettempdir():
            stats_filename = os.path.join(self.output_folder, f"state_variety_statistics_lp_{plan_year}.csv")
            self.state_variety_statistics.to_csv(stats_filename, index=False)
            print(f"\nStatistics saved to: {stats_filename}")
        else:
            # API execution - skip CSV save
            logger.debug("Skipping statistics CSV save (API execution mode)")

    def generate_both_plans(self):
        plans = {}
        plans['2024-2025'] = self.generate_plan(2024)
        plans['2025-2026'] = self.generate_plan(2025)
        return plans

    def execute_with_ui_selections(
        self,
        method: str,
        criteria: str,
        plan_revision_version: str,
        season_id: str,
        crop_id: str,
        state_variety_targets: Dict[str, Dict[str, float]],
        criteria_config: Optional[List[Dict]] = None,
        constraints: Optional[List[Dict]] = None
    ) -> pd.DataFrame:
        """
        Execute planning with UI selections via plan_execution.py.
        Overrides targets with UI payload - NO data filtering, NO DB/CSV saves.
        Uses exact same allocation logic as generate_plan().
        """
        import re
        from datetime import datetime
        
        # Extract plan_year from season_id
        def extract_plan_year_from_season_id(season_id: str) -> int:
            match = re.search(r'(\d{2})[_-](\d{2})', season_id)
            if match:
                return 2000 + int(match.group(1))
            return datetime.now().year
        
        plan_year = extract_plan_year_from_season_id(season_id)
        
        # Ensure data is loaded before proceeding
        if self.data.empty:
            logger.info("Data not loaded, calling load_from_database()")
            self.load_from_database()
        
        # Convert variety_ids to variety names for allocation
        variety_ids_list = []
        for varieties in state_variety_targets.values():
            variety_ids_list.extend(varieties.keys())
        unique_variety_ids = list(set(variety_ids_list))
        
        variety_id_to_name_map = {}
        if unique_variety_ids:
            try:
                variety_placeholders = ','.join([f':var_{i}' for i in range(len(unique_variety_ids))])
                variety_query = text(f"""
                    SELECT variety_id, variety_name 
                    FROM operations.varieties 
                    WHERE variety_id IN ({variety_placeholders})
                """)
                variety_params = {f'var_{i}': var_id for i, var_id in enumerate(unique_variety_ids)}
                variety_results = self.db.execute(variety_query, variety_params).fetchall()
                variety_id_to_name_map = {str(row[0]): str(row[1]).strip() for row in variety_results if row[0] and row[1]}
            except Exception as e:
                logger.warning(f"Could not fetch variety names: {e}")
        
        # Override targets with UI payload (convert variety_id to variety_name USRH codes)
        self.state_variety_targets = {}
        self.state_variety_targets_kgs = {}
        for state, varieties in state_variety_targets.items():
            self.state_variety_targets[state] = {}
            self.state_variety_targets_kgs[state] = {}
            for variety_id, target_mt in varieties.items():
                variety_name = variety_id_to_name_map.get(variety_id, variety_id)
                normalized_variety = self.normalize_variety_to_us_code(variety_name)
                if target_mt > 0:
                    self.state_variety_targets[state][normalized_variety] = target_mt
                    self.state_variety_targets_kgs[state][normalized_variety] = target_mt * 1000
        
        # Mark that targets are set from UI (prevent generate_plan from overwriting)
        self.targets_set_from_ui = True
        
        # Disable saves for API execution (but keep print statements for terminal)
        original_save_to_db = self.save_plan_to_database
        original_output_folder = self.output_folder
        
        def no_save_to_db(*args, **kwargs):
            return True
        
        self.output_folder = tempfile.gettempdir()
        
        # Keep print_state_variety_statistics for terminal output, but disable CSV save
        original_print_stats = self.print_state_variety_statistics
        
        def print_stats_no_csv(plan_year_arg):
            if not hasattr(self, 'state_variety_statistics') or self.state_variety_statistics.empty:
                print("\nNo state-variety statistics available")
                return
            
            print("\n" + "=" * 100)
            print(f"STATE-VARIETY ALLOCATION STATISTICS (Plan Year: {plan_year_arg})")
            print("=" * 100)
            
            for state in sorted(self.state_variety_statistics['state'].unique()):
                state_data = self.state_variety_statistics[self.state_variety_statistics['state'] == state]
                if state_data.empty:
                    continue
                
                print(f"\n{state.upper()}:")
                print("-" * 100)
                print(f"{'Variety':<15} {'Target (MT)':<15} {'Allocated (MT)':<18} {'% Achieved':<12} {'Acres':<12} {'Cost (Rs)':<15} {'Status':<20}")
                print("-" * 100)
                
                for _, row in state_data.iterrows():
                    target_mt = row['target_mt']
                    allocated_mt = row['allocated_mt']
                    pct_achieved = (allocated_mt / target_mt * 100) if target_mt > 0 else 0.0
                    print(f"{row['variety']:<15} {target_mt:>12.2f} {allocated_mt:>15.2f} {pct_achieved:>10.1f}% "
                          f"{row['allocated_acres']:>10.2f} {row['total_cost']:>13,.0f} {row['status']:<20}")
        
        self.save_plan_to_database = no_save_to_db
        self.print_state_variety_statistics = print_stats_no_csv
        
        try:
            # Call generate_plan with overridden targets - uses exact same allocation logic
            result_df = self.generate_plan(plan_year)
            
            # Filter result to ONLY include payload state-variety combinations
            if not result_df.empty and 'state' in result_df.columns and 'variety' in result_df.columns:
                # Get target states and varieties from UI payload
                target_states = list(self.state_variety_targets.keys())
                target_varieties = set()
                for varieties in self.state_variety_targets.values():
                    target_varieties.update(varieties.keys())
                
                # Normalize states for matching (case-insensitive)
                # MUST use normalize_state_name() to ensure districts (Karimnagar, Warangal) map to states (Telangana)
                result_df_normalized = result_df.copy()
                result_df_normalized['state_normalized'] = result_df_normalized['state'].apply(
                    lambda x: self.normalize_state_name(str(x)).strip().lower() if pd.notna(x) else ''
                )
                target_states_normalized = [self.normalize_state_name(s).strip().lower() for s in target_states]
                
                # Filter by state (normalized matching)
                state_filter = result_df_normalized['state_normalized'].isin(target_states_normalized)
                
                # Filter by variety (normalize to USRH codes for matching)
                variety_filter = pd.Series([False] * len(result_df), index=result_df.index)
                for idx, row in result_df.iterrows():
                    village_variety = str(row.get('variety', ''))
                    normalized_variety = self.normalize_variety_to_us_code(village_variety)
                    if normalized_variety in target_varieties:
                        variety_filter.loc[idx] = True
                
                # Combine filters: state AND variety must match
                combined_filter = state_filter & variety_filter
                result_df = result_df[combined_filter].copy().reset_index(drop=True)
                
                logger.info(f"Filtered result to {len(result_df)} rows matching payload state-variety combinations")
            
            return result_df
        finally:
            self.save_plan_to_database = original_save_to_db
            self.output_folder = original_output_folder
            self.print_state_variety_statistics = original_print_stats
            self.targets_set_from_ui = False


def main():
    """
    Main function to generate two-stage LP optimized supply chain plans
    Loads data from database (schema: operations) only
    """
    print("=" * 80)
    print("SUPPLY CHAIN PLANNING - TWO-STAGE LINEAR PROGRAMMING (DATABASE)")
    print("Schema: operations | Table: season_crop_inspection_final")
    print("=" * 80)
    
    try:
        # Connect to database
        db = next(get_db())
        print("\n[OK] Database connection established")
        planner = SupplyChainPlanner(db=db)
        
        try:
            plans = planner.generate_both_plans()
            
            # Display summary for each plan
            print("\n" + "=" * 80)
            print("SUMMARY OF ALL PLANS")
            print("=" * 80)
            
            for name, df in plans.items():
                if not df.empty:
                    total_prod = df['adjusted_production_allocation'].sum() if 'adjusted_production_allocation' in df.columns else 0
                    total_cost = df['probable_cost'].sum() if 'probable_cost' in df.columns else 0
                    total_acres = df['allocated_acres'].sum() if 'allocated_acres' in df.columns else 0
                    num_villages = df['village'].nunique() if 'village' in df.columns else 0
                    num_varieties = df['variety'].nunique() if 'variety' in df.columns else 0
                    
                    print(f"\n{name} Plan:")
                    print(f"  Entries: {len(df)}")
                    print(f"  Villages: {num_villages}")
                    print(f"  Varieties: {num_varieties}")
                    print(f"  Production: {total_prod:,.0f} Kg ({total_prod/1_000_000:.2f} MT)")
                    print(f"  Cost: Rs {total_cost:,.0f}")
                    print(f"  Acres: {total_acres:,.2f}")
                else:
                    print(f"\n{name} Plan: No data generated")
            
            # Verify saved plans
            print("\n" + "=" * 80)
            print("VERIFYING SAVED PLANS IN DATABASE")
            print("=" * 80)
            
            for plan_year in [2024, 2025]:
                verification = planner.verify_saved_plan(plan_year=plan_year)
                if verification['success']:
                    print(f"\nYear {plan_year}: Found {verification['total_records']} records")
                    if verification.get('plan_version'):
                        print(f"   Latest Version: {verification['plan_version']}")
                    if verification.get('sample_records'):
                        print(f"   Sample villages: {', '.join([r['village'] for r in verification['sample_records'][:3]])}")
                else:
                    print(f"\nYear {plan_year}: No records found in database")
                    if verification.get('error'):
                        print(f"   Error: {verification['error']}")
            
            print("\n" + "=" * 80)
            print("PLANNING COMPLETED")
            print("=" * 80)
            print("\n To verify manually, query the database:")
            print("   SELECT COUNT(*) FROM operations.supply_chain_planning;")
            print("   SELECT * FROM operations.supply_chain_planning ORDER BY created_at DESC LIMIT 10;")
            
        finally:
            db.close()
            
    except Exception as e:
        print(f"\n[ERROR] Failed to generate plans: {str(e)}")
        print("Please ensure database connection is properly configured.")
        raise


if __name__ == "__main__":
    main() 
