"""
Plan Execution Orchestration Layer

Coordinates planning execution between API endpoints and planning services.
UI payload (season, crop, state, variety, targets) is the single source of truth.
"""

import re
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)


class PlanExecutionOrchestrator:
    """
    Orchestration layer for planning execution.
    Handles UI payload processing and routes to appropriate planning services.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def execute_plan(
        self,
        plan_revision_version: str,
        season_id: str,
        crop_id: str,
        targets: List[Dict[str, Any]],
        method_algorithm: str,
        criteria: Optional[List[Dict[str, Any]]] = None,
        constraints: Optional[List[Dict[str, Any]]] = None,
        progress_callback: Optional[callable] = None
    ) -> pd.DataFrame:
        """
        Execute planning with UI payload as single source of truth.
        
        Args:
            plan_revision_version: Plan version string
            season_id: Season ID
            crop_id: Crop ID
            targets: List of target items with state, variety_id, quantity
            method_algorithm: Planning method (maximization, minimization, min-max)
            criteria: Optional criteria configuration
            constraints: Optional constraints
            
        Returns:
            DataFrame with plan results
        """
        # Extract year range (YYYY-YYYY) from plan_revision_version or season_id
        if progress_callback:
            progress_callback("extract_year", "Extracting plan year range", {"plan_revision_version": plan_revision_version, "season_id": season_id})
        
        plan_year_range = self._extract_year_range(plan_revision_version, season_id)
        
        if progress_callback:
            progress_callback("build_targets", "Building state-variety targets", {"plan_year_range": plan_year_range})
        
        # Derive states from operations.targets (single source of truth)
        state_variety_targets = self._build_state_variety_targets(
            plan_year_range, season_id, crop_id, targets
        )
        
        if progress_callback:
            progress_callback("targets_built", "State-variety targets built", {
                "states_count": len(state_variety_targets),
                "total_varieties": sum(len(v) for v in state_variety_targets.values())
            })
        
        # Allow empty state_variety_targets if all targets had quantity=0
        # (Planning execution should skip allocation for targets where quantity = 0 instead of failing)
        if not state_variety_targets or sum(len(v) for v in state_variety_targets.values()) == 0:
            logger.info(
                f"No targets with non-zero quantities to process. "
                f"All targets had quantity=0 or were skipped. Proceeding with empty allocation."
            )
            # Return empty DataFrame instead of failing (will result in zero allocations)
            return pd.DataFrame()
        
        # Get selected criteria (criteria is a list of dicts)
        selected_criteria = criteria[0].get("id", "productivity").lower() if criteria and len(criteria) > 0 else "productivity"
        
        # Validate constraints BEFORE execution
        # Calculate total target quantity across all targets
        total_target_quantity_kgs = 0.0
        for state, varieties in state_variety_targets.items():
            for variety_id, target_mt in varieties.items():
                total_target_quantity_kgs += target_mt * 1000  # Convert MT to kgs
        
        # Check max_allocation constraint if provided
        max_allocation = None
        if criteria and len(criteria) > 0:
            max_allocation = criteria[0].get("max_allocation", 0)
            if max_allocation and max_allocation > 0:
                if total_target_quantity_kgs > max_allocation:
                    logger.warning(
                        f"Total target quantity ({total_target_quantity_kgs:,.0f} kgs) exceeds "
                        f"max_allocation constraint ({max_allocation:,.0f} kgs). "
                        f"Execution will proceed but allocation may be limited."
                    )
                    # Optionally: raise error or auto-adjust
                    # For now, we'll proceed and let the planning service handle it
        
        # Check Target_Budget constraint if provided
        target_budget = None
        if constraints:
            for constraint in constraints:
                if constraint.get("id", "").lower() in ["target_budget", "target_budget_constraint"]:
                    target_budget = constraint.get("value", 0)
                    logger.info(f"Target_Budget constraint: {target_budget:,.0f}")
        
        # Prepare criteria and constraints config
        # NOTE: max_allocation should be applied per-target, not globally
        # For now, we pass it but planning service should handle per-target
        criteria_config = []
        if criteria:
            criteria_config = [
                {"id": c.get("id", ""), "min_allocation": c.get("min_allocation", 0), 
                 "max_allocation": c.get("max_allocation", 0)} 
                for c in criteria
            ]
            logger.info(f"Criteria config: {criteria_config}")
        
        constraints_config = []
        if constraints:
            constraints_config = [
                {"id": c.get("id", ""), "value": c.get("value", 0)} 
                for c in constraints
            ]
            logger.info(f"Constraints config: {constraints_config}")
        
        # Route to appropriate planning service
        method_id = method_algorithm.strip().lower()
        
        if progress_callback:
            progress_callback("route_service", f"Routing to {method_id} planning service", {"method": method_id})
        
        try:
            if method_id == "maximization":
                from planning_methods.supply_chain_planning_service import SupplyChainPlanner
                planner = SupplyChainPlanner(self.db)
                if progress_callback:
                    progress_callback("service_execution", "Executing maximization planning", {"stage": "planning_start"})
                result_df = planner.execute_with_ui_selections(
                    method=method_id,
                    criteria=selected_criteria,
                    plan_revision_version=plan_revision_version,
                    season_id=season_id,
                    crop_id=crop_id,
                    state_variety_targets=state_variety_targets,
                    criteria_config=criteria_config,
                    constraints=constraints_config
                )
                if progress_callback:
                    progress_callback("service_complete", "Maximization planning completed", {"stage": "planning_complete", "result_rows": len(result_df) if isinstance(result_df, pd.DataFrame) else 0})
                
            elif method_id == "minimization":
                from planning_methods.minimization_planning_service import MinimizationPlanner
                planner = MinimizationPlanner(self.db)
                if progress_callback:
                    progress_callback("service_execution", "Executing minimization planning", {"stage": "planning_start"})
                result_df = planner.execute_with_ui_selections(
                    method=method_id,
                    criteria=selected_criteria,
                    plan_revision_version=plan_revision_version,
                    season_id=season_id,
                    crop_id=crop_id,
                    state_variety_targets=state_variety_targets,
                    criteria_config=criteria_config,
                    constraints=constraints_config
                )
                if progress_callback:
                    progress_callback("service_complete", "Minimization planning completed", {"stage": "planning_complete", "result_rows": len(result_df) if isinstance(result_df, pd.DataFrame) else 0})
                
            elif method_id in ["min-max", "min_max", "minmax"]:
                from planning_methods.Linear_programming import SupplyChainPlanner
                planner = SupplyChainPlanner(self.db)
                if progress_callback:
                    progress_callback("service_execution", "Executing min-max planning", {"stage": "planning_start"})
                result_df = planner.execute_with_ui_selections(
                    method=method_id,
                    criteria=selected_criteria,
                    plan_revision_version=plan_revision_version,
                    season_id=season_id,
                    crop_id=crop_id,
                    state_variety_targets=state_variety_targets,
                    criteria_config=criteria_config,
                    constraints=constraints_config
                )
                if progress_callback:
                    progress_callback("service_complete", "Min-max planning completed", {"stage": "planning_complete", "result_rows": len(result_df) if isinstance(result_df, pd.DataFrame) else 0})
            else:
                raise ValueError(f"Invalid method_algorithm: {method_id}. Must be maximization, minimization, or min_max")
        except Exception as e:
            # Log the error but don't fail - we'll return empty results with proper logging
            logger.error(f"Planning execution failed: {str(e)}")
            logger.exception(e)
            # Return empty DataFrame - endpoint will handle zero allocation case
            result_df = pd.DataFrame()
            logger.warning("Returning empty DataFrame due to execution error - will be handled as zero allocation case")
        
        # Validate result and log allocation status per target
        if isinstance(result_df, pd.DataFrame) and not result_df.empty:
            logger.info(f"Execution completed: {len(result_df)} allocation rows returned")
            # Log allocation status per target
            if 'state' in result_df.columns and 'variety' in result_df.columns:
                result_grouped = result_df.groupby(['state', 'variety']).size()
                logger.info(f"Allocations by state-variety:\n{result_grouped}")
        else:
            logger.warning("Execution returned empty or invalid result - all targets will show zero allocation")
        
        return result_df
    
    def _extract_year_range(self, plan_revision_version: str, season_id: str) -> str:
        """
        Extract year range (YYYY-YYYY) from plan_revision_version or season_id.
        
        Args:
            plan_revision_version: Plan version string (e.g., "v1.0-Maximized-Productivity")
            season_id: Season ID (e.g., "RABI_24_25")
            
        Returns:
            Year range string in format "YYYY-YYYY" (e.g., "2024-2025")
        """
        # Try to extract from season_id first (e.g., "RABI_24_25" -> "2024-2025")
        season_match = re.search(r'(\d{2})[_-](\d{2})', season_id)
        if season_match:
            year1 = int(season_match.group(1))
            year2 = int(season_match.group(2))
            # Handle 2-digit years: assume 20xx format
            full_year1 = 2000 + year1 if year1 < 100 else year1
            full_year2 = 2000 + year2 if year2 < 100 else year2
            return f"{full_year1}-{full_year2}"
        
        # Try to extract from plan_revision_version (e.g., "v1.0-Maximized-Productivity-2024-2025")
        version_match = re.search(r'(\d{4})[_-](\d{4})', plan_revision_version)
        if version_match:
            return f"{version_match.group(1)}-{version_match.group(2)}"
        
        # Try to extract single year and construct range
        single_year_match = re.search(r'(\d{4})', plan_revision_version)
        if single_year_match:
            year = int(single_year_match.group(1))
            return f"{year}-{year + 1}"
        
        # Fallback: use current year
        current_year = datetime.now().year
        return f"{current_year}-{current_year + 1}"
    
    def _build_state_variety_targets(
        self,
        plan_year_range: str,
        season_id: str,
        crop_id: str,
        targets: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, float]]:
        """
        Build state_variety_targets dictionary from UI payload.
        Payload state is the single source of truth - validates against operations.targets.
        
        Args:
            plan_year_range: Plan year range in format "YYYY-YYYY" (e.g., "2024-2025")
            season_id: Season ID
            crop_id: Crop ID
            targets: List of target items from UI payload with state, variety_id, quantity
            
        Returns:
            Dictionary in format {state: {variety_id: target_MT}}
        """
        # Extract unique variety_ids from payload for DB query
        payload_variety_ids = list(set([
            t.get("variety_id") if isinstance(t, dict) else getattr(t, 'variety_id', None) 
            for t in targets 
            if (t.get("variety_id") if isinstance(t, dict) else getattr(t, 'variety_id', None))
        ]))
        
        if not payload_variety_ids:
            raise ValueError("No valid variety_ids found in targets payload")
        
        # Fetch all valid (state, variety_id) combinations from operations.targets for validation
        conditions = []
        params = {}
        
        conditions.append("t.plan_year = :plan_year_range")
        params["plan_year_range"] = plan_year_range
        
        conditions.append("t.season_id = :season_id")
        params["season_id"] = season_id
        
        conditions.append("t.crop_id = :crop_id")
        params["crop_id"] = crop_id
        
        # Filter by variety_ids from payload
        variety_conditions = []
        for i, var_id in enumerate(payload_variety_ids):
            variety_conditions.append(f"t.variety_id = :var_id_{i}")
            params[f'var_id_{i}'] = var_id
        conditions.append(f"({' OR '.join(variety_conditions)})")
        
        where_clause = " AND ".join(conditions)
        
        targets_query = text(f"""
            SELECT DISTINCT 
                t.state,
                t.variety_id
            FROM operations.targets t
            WHERE {where_clause}
            AND t.state IS NOT NULL 
            AND t.state != ''
            AND t.variety_id IS NOT NULL 
            AND t.variety_id != ''
            ORDER BY t.state, t.variety_id
        """)
        
        try:
            targets_results = self.db.execute(targets_query, params).fetchall()
        except Exception as e:
            logger.error(f"Error fetching states from operations.targets: {e}")
            raise ValueError(
                f"Could not fetch states from operations.targets for "
                f"season_id={season_id}, crop_id={crop_id}: {str(e)}"
            )
        
        # Helper function to normalize state names (handle common variations)
        # MUST match the normalization logic from SupplyChainPlanner.normalize_state_name()
        # This ensures district-level states (Karimnagar, Warangal) are normalized to Telangana
        def normalize_state_name(state_name: str) -> str:
            """
            Normalize state name for comparison, handling common variations.
            Matches the behavior of SupplyChainPlanner.normalize_state_name() to ensure
            district-level states (Karimnagar, Warangal) are normalized to Telangana.
            """
            if not state_name:
                return ''
            normalized = str(state_name).strip().lower()
            # Map common variations - MUST include district-to-state mappings
            state_mapping = {
                'chhattisgarh': 'chattissgarh',
                'chattisgarh': 'chattissgarh',
                'chattissgarh': 'chattissgarh',
                'odisha': 'odisha',
                'orissa': 'odisha',
                'karnataka': 'karnataka',
                'warangal': 'telangana',  # Warangal is a district in Telangana
                'karimnagar': 'telangana',  # Karimnagar is a district in Telangana
                'telangana': 'telangana',
                'andhra pradesh': 'andhra pradesh',
                'andhra': 'andhra pradesh',
                'telengana': 'telangana'
            }
            return state_mapping.get(normalized, normalized)
        
        # Create set of valid (state, variety_id) combinations from DB for validation
        # Normalize states using the same normalization function to ensure districts map to states
        valid_state_variety_combos = set()
        state_normalization_map = {}  # Maps normalized -> actual DB state name
        
        for row in targets_results:
            db_state = str(row[0]).strip() if row[0] else None
            variety_id = str(row[1]).strip() if row[1] else None
            if db_state and variety_id:
                # Normalize DB state using the same function (districts -> states)
                normalized_state = normalize_state_name(db_state)
                valid_state_variety_combos.add((normalized_state, variety_id))
                # Store mapping from normalized to actual DB state (preserve first occurrence)
                # This allows us to use the exact DB state name in the final output
                if normalized_state not in state_normalization_map:
                    state_normalization_map[normalized_state] = db_state
        
        # Build state_variety_targets using payload state as source of truth
        state_variety_targets = {}
        skipped_targets = []
        processed_targets = []
        
        for target in targets:
            # Handle both dict and object access
            if isinstance(target, dict):
                variety_id = target.get("variety_id")
                quantity_kgs = target.get("quantity", 0.0)
                payload_state = target.get("state")
            else:
                variety_id = getattr(target, 'variety_id', None)
                quantity_kgs = getattr(target, 'quantity', 0.0)
                payload_state = getattr(target, 'state', None)
            
            if not variety_id:
                skipped_targets.append(f"Missing variety_id")
                logger.warning(f"Skipping target with missing variety_id: {target}")
                continue
            
            if not payload_state:
                skipped_targets.append(f"variety_id={variety_id} (missing state)")
                logger.warning(
                    f"Target with variety_id={variety_id} is missing state. Skipping."
                )
                continue
            
            # Normalize payload state for validation
            payload_state_normalized = normalize_state_name(payload_state)
            
            # Check if (state, variety_id) combination exists in operations.targets
            combo_exists = (payload_state_normalized, variety_id) in valid_state_variety_combos
            
            # Handle zero quantity: skip allocation instead of failing
            # Planning execution should skip allocation for targets where quantity = 0 instead of failing
            if quantity_kgs == 0.0:
                if combo_exists:
                    # Valid zero quantity - skip allocation (don't add to targets, but don't fail)
                    logger.info(
                        f"Skipping target with quantity=0: state='{payload_state}', variety_id='{variety_id}'. "
                        f"Combination exists in operations.targets, skipping allocation."
                    )
                else:
                    # Zero quantity for non-existent combination - skip silently
                    logger.info(
                        f"Skipping target with quantity=0: state='{payload_state}', variety_id='{variety_id}'. "
                        f"Combination does not exist in operations.targets, skipping allocation."
                    )
                continue
            
            # For non-zero quantities: validate that (state, variety_id) combination exists
            # Validation fails only when non-zero quantity is sent for combination that doesn't exist
            if not combo_exists:
                # Get available states for this variety_id for error message
                available_states = [
                    state_normalization_map.get(norm_state, norm_state)
                    for norm_state, var_id in valid_state_variety_combos
                    if var_id == variety_id
                ]
                error_msg = (
                    f"Invalid state-variety combination: state='{payload_state}', variety_id='{variety_id}'. "
                    f"This combination does not exist in operations.targets for "
                    f"season_id={season_id}, crop_id={crop_id}, plan_year={plan_year_range}. "
                    f"Available states for variety_id '{variety_id}': {available_states if available_states else 'none'}"
                )
                skipped_targets.append(f"state='{payload_state}', variety_id={variety_id} (non-zero quantity)")
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            # Use payload state as source of truth - get actual DB state name for consistency
            # (use the normalized mapping to get the exact DB state name)
            actual_db_state = state_normalization_map.get(payload_state_normalized, payload_state.strip())
            
            quantity_mt = quantity_kgs / 1000.0  # Convert Kgs to MT
            
            # Initialize state dict if needed
            if actual_db_state not in state_variety_targets:
                state_variety_targets[actual_db_state] = {}
            
            # Handle duplicate targets for same state+variety (sum quantities if same)
            if variety_id in state_variety_targets[actual_db_state]:
                old_quantity = state_variety_targets[actual_db_state][variety_id]
                state_variety_targets[actual_db_state][variety_id] = old_quantity + quantity_mt
                logger.info(
                    f"Duplicate target for state={actual_db_state}, variety_id={variety_id}. "
                    f"Summing quantities: {old_quantity} MT + {quantity_mt} MT = "
                    f"{state_variety_targets[actual_db_state][variety_id]} MT"
                )
            else:
                state_variety_targets[actual_db_state][variety_id] = quantity_mt
                logger.info(
                    f"Added target: variety_id={variety_id}, state={actual_db_state}, "
                    f"quantity={quantity_mt} MT ({quantity_kgs} kgs)"
                )
            
            # Track that this target was processed
            processed_targets.append({
                'variety_id': variety_id,
                'state': actual_db_state,
                'quantity_mt': quantity_mt
            })
        
        # Log summary of skipped targets (should be empty if validation passed)
        # skipped_targets only contains non-zero quantities that failed validation
        if skipped_targets:
            logger.error(
                f"Skipped {len(skipped_targets)} target(s): {', '.join(skipped_targets)}"
            )
            raise ValueError(
                f"Failed to process {len(skipped_targets)} target(s). "
                f"Please verify state and variety_id combinations exist in operations.targets table for "
                f"season_id={season_id}, crop_id={crop_id}, plan_year={plan_year_range}. "
                f"Skipped: {', '.join(skipped_targets)}"
            )
        
        # Allow empty state_variety_targets if all targets had quantity=0
        # (Planning execution should skip allocation for targets where quantity = 0 instead of failing)
        total_varieties = sum(len(v) for v in state_variety_targets.values())
        if total_varieties == 0:
            logger.info("No targets with non-zero quantities to process (all targets had quantity=0, skipping allocation)")
        
        # Log final state_variety_targets for debugging
        logger.info(
            f"Successfully built state_variety_targets: "
            f"{len(targets)} input target(s), "
            f"{total_varieties} unique variety(ies) across "
            f"{len(state_variety_targets)} state(s)"
        )
        
        # Log detailed breakdown by state - this is critical for debugging
        for state, varieties in state_variety_targets.items():
            logger.info(f"  State '{state}': {len(varieties)} variety(ies)")
            total_for_state = sum(varieties.values())
            for var_id, target_mt in varieties.items():
                logger.info(f"    - variety_id={var_id}: {target_mt} MT ({target_mt*1000} kgs)")
            logger.info(f"    Total for {state}: {total_for_state} MT ({total_for_state*1000} kgs)")
        
        # Log the exact dictionary structure being passed to planning service
        logger.info(f"Final state_variety_targets structure: {state_variety_targets}")
        
        return state_variety_targets

