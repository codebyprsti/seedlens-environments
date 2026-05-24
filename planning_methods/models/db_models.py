from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Float, Boolean, Text, Index, Date, Numeric
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from core.db import Base, engine


class CategoryRecord(Base):
    __tablename__ = "categories"
    __table_args__ = {'schema': 'operations'}

    category_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    category_name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    crops = relationship("CropRecord", back_populates="category")
    varieties = relationship("VarietyRecord", back_populates="category")
    locations = relationship("LocationRecord", back_populates="category")
    growers = relationship("GrowerRecord", back_populates="category")
    organizers = relationship("OrganizerRecord", back_populates="category")
    # inspections = relationship("SeasonCropInspectionBase", back_populates="category")
    column_metadata = relationship("ColumnMetadataRecord", back_populates="category")


class ColumnMetadataRecord(Base):
    __tablename__ = "column_metadata_records"
    __table_args__ = {'schema': 'operations'}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    db_column_name = Column(String(100), nullable=False)
    display_name = Column(String(100), nullable=False)
    type = Column(String(50), nullable=False)
    group_name = Column(String(100), nullable=True)
    is_visible = Column(Boolean, default=True, nullable=False)
    category_id = Column(Integer, ForeignKey("operations.categories.category_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="column_metadata")


class CropRecord(Base):
    __tablename__ = "crops"
    __table_args__ = {'schema': 'operations'}

    crop_id: Mapped[str] = mapped_column(String(20), primary_key=True, unique=True, index=True)
    crop_name: Mapped[str] = mapped_column(String(100), nullable=False)
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("operations.categories.category_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="crops")
    varieties = relationship("VarietyRecord", back_populates="crop")
    inspections = relationship("SeasonCropInspectionBase", back_populates="crop_record")


class VarietyRecord(Base):
    __tablename__ = "varieties"
    __table_args__ = {'schema': 'operations'}

    variety_id = Column(String(20), primary_key=True, unique=True, index=True, nullable=False)
    variety_name = Column(String(100), nullable=False)
    crop_id = Column(String(20), ForeignKey("operations.crops.crop_id"), nullable=False)
    category_id = Column(Integer, ForeignKey("operations.categories.category_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="varieties")
    crop = relationship("CropRecord", back_populates="varieties")
    inspections = relationship("SeasonCropInspectionBase", back_populates="variety")


class LocationRecord(Base):
    __tablename__ = "locations"
    __table_args__ = {'schema': 'operations'}

    location_id = Column(String(20), unique=True, primary_key=True, index=True, nullable=False)
    source_location_id = Column(String(100))
    village = Column(String(100), nullable=False)
    unique_location_id =  Column(String(100), nullable=False)
    mandal = Column(String(100))
    mandal_id = Column(Integer)
    district = Column(String(100))
    district_id = Column(Integer)
    state = Column(String(100))
    category_id = Column(Integer, ForeignKey("operations.categories.category_id"), nullable=False)
    latitude = Column(Float)
    longitude = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="locations")
    inspections = relationship("SeasonCropInspectionBase", back_populates="location")

class OrganizerRecord(Base):
    __tablename__ = "organizers"
    __table_args__ = {'schema': 'operations'}

    organizer_id = Column(String(20), unique=True, primary_key=True, index=True, nullable=False)
    source_organizer_id = Column(String(100))
    organizer_name = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey("operations.categories.category_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="organizers")
    inspections = relationship("SeasonCropInspectionBase", back_populates="organizer")


class GrowerRecord(Base):
    __tablename__ = "growers"
    __table_args__ = {'schema': 'operations'}

    grower_id = Column(String(20), unique=True, primary_key=True, index=True, nullable=False)
    source_grower_id = Column(String(100))
    grower_name = Column(String(100), nullable=False)
    fathers_name = Column(String(100))
    grower_gender = Column(String(10))
    category_id = Column(Integer, ForeignKey("operations.categories.category_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="growers")
    inspections = relationship("SeasonCropInspectionBase", back_populates="grower")


# class OrganizerRecord(Base):
#     __tablename__ = "organizers"
#     __table_args__ = {'schema': 'operations'}
#
#     organizer_id = Column(String(20), unique=True, primary_key=True, index=True, nullable=False)
#     organizer_name = Column(String(100), nullable=False)
#     source_organizer_id = Column(String(20), nullable=True)
#     category_id = Column(Integer, ForeignKey("operations.categories.category_id"), nullable=False)
#     production_plant = Column(String(100))
#     created_at = Column(DateTime, default=datetime.utcnow)
#     updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
#
#     # Relationships
#     category = relationship("CategoryRecord", back_populates="organizers")


class SeasonCropInspectionFinal(Base):
    __tablename__ = "season_crop_inspection_final"
    __table_args__ = {'schema': 'operations'}

    id = Column(Integer, primary_key=True, index=True)

    season = Column(String)
    crop = Column(String)
    hsp_code = Column(String)
    hybrid_id = Column(String)
    lot_no_batch_no = Column(String)
    org_id = Column(String)
    organizer_name = Column(String)
    grower_id = Column(String)
    grower_name = Column(String)
    grower_gender = Column(String)
    purchasing_document_number = Column(String)
    father_s_name = Column(String)
    village = Column(String)
    village_id = Column(String)
    mandal = Column(String)
    mandal_id = Column(String)
    district = Column(String)
    district_id = Column(String)
    state = Column(String)
    male_parent_seed_lot_no = Column(String)
    male_soaking_acre = Column(Float)
    male_no_of_pkt = Column(Float)
    male_qty_in_kgs_ = Column(Float)
    female_parent_seed_lot_no = Column(String)
    female_soaking_acre = Column(Float)
    female_no_of_pkt= Column(Float)  # renamed to avoid conflict
    female_qty_in_kgs = Column(Float)
    production_officer = Column(String)
    tfa_name = Column(String)

    inspection_1_date_of_inspection = Column(DateTime)
    inspection_1_male_1_date_of_soaking = Column(DateTime)
    inspection_1_male_2_date_of_soaking = Column(DateTime)
    inspection_1_male_3_date_of_soaking = Column(DateTime)
    inspection_1_female_date_of_soaking = Column(DateTime)
    inspection_1_germination = Column(Float)
    inspection_1_male_date_of_transplant = Column(DateTime)
    inspection_1_female_date_of_transplant = Column(DateTime)
    inspection_1_male_female_row_ration = Column(String)
    inspection_1_plough_down = Column(String)
    inspection_1_disease_or_pest_attack = Column(String)
    inspection_1_isolation_distance = Column(Float)
    inspection_1_chilling_injury = Column(String)
    inspection_1_soil_type = Column(String)
    inspection_1_parent_seed_return = Column(String)
    inspection_1_nursery_beds = Column(String)
    inspection_1_humidity = Column(Float)
    inspection_1_temperatur = Column(Float)
    inspection_1_date_and_time = Column(DateTime)

    inspection_2_date_of_inspection = Column(DateTime)
    inspection_2_net_acre = Column(Float)
    inspection_2_pld_acres = Column(Float)
    inspection_2_gps_net_acres = Column(Float)
    inspection_2_water_availability = Column(String)
    inspection_2_water_level = Column(Float)
    inspection_2_plough_down = Column(String)
    inspection_2_disease_or_pest_attack = Column(String)
    inspection_2_plant_per_sq_meter = Column(Float)
    inspection_2_tillage = Column(String)
    inspection_2_residual_crop_removal = Column(String)
    inspection_2_farmer_trained_for_csa = Column(String)
    inspection_2_fym_application = Column(String)
    inspection_2_mt_acre = Column(Float)
    inspection_2_form_of_n = Column(String)
    inspection_2_humidity = Column(Float)
    inspection_2_temperature = Column(Float)
    inspection_2_date_and_time = Column(DateTime)
    inspection_2_image = Column(String)

    inspection_3_date_of_inspection = Column(DateTime)
    inspection_3_date_of_ppi_male_1 = Column(DateTime)
    inspection_3_date_of_ppi_male_2 = Column(DateTime)
    inspection_3_date_of_ppi_female = Column(DateTime)
    inspection_3_nick_status = Column(String)
    inspection_3_complaiance_to_sop_or_not = Column(String)
    inspection_3_water_availability = Column(String)
    inspection_3_water_level = Column(Float)
    inspection_3_no_of_tillers = Column(Integer)
    inspection_3_plough_down = Column(String)
    inspection_3_complaiance_to_sop_or_not_2 = Column(String)  # renamed to avoid duplicate
    inspection_3_disease_or_pest_attack = Column(String)
    inspection_3_humidity = Column(Float)
    inspection_3_temperature = Column(Float)
    inspection_3_date_and_time = Column(DateTime)
    inspection_3_image = Column(String)

    inspection_4_date_of_inspection = Column(DateTime)
    inspection_4_date_of_50_flowering_male = Column(DateTime)
    inspection_4_date_of_50_flowering_female = Column(DateTime)
    inspection_4_date_of_pollination_start = Column(DateTime)
    inspection_4_week = Column(String)
    inspection_4_start_acres = Column(Float)
    inspection_4_water_availability = Column(String)
    inspection_4_water_level = Column(Float)
    inspection_4_avg_no_of_grains_panicle = Column(Float)
    inspection_4_avg_number_of_productive_tillers = Column(Float)
    inspection_4_total_number_of_floret_per = Column(Float)
    inspection_4_plough_down = Column(String)
    inspection_4_disease_or_pest_attack = Column(String)
    inspection_4_parental_purity_male = Column(String)
    inspection_4_parental_purity_female = Column(String)
    inspection_4_rouging_ensure_to_remove_the_off_type_plants = Column(String)
    inspection_4_mode_of_pollination = Column(String)
    inspection_4_humidity = Column(Float)
    inspection_4_temperature = Column(Float)
    inspection_4_date_and_time = Column(DateTime)
    inspection_4_total_number_of_floret_per_panicle_opened = Column(Float)
    inspection_4_time_of_floret_opening = Column(String)
    inspection_4_is_pollen_available_during_floret_opening_yes_no_ = Column(String)

    inspection_5_date_of_inspection = Column(DateTime)
    inspection_5_date_of_pollination_stop = Column(DateTime)
    inspection_5_end_acres = Column(Float)
    inspection_5_avg_no_of_grains_panicle = Column(Float)
    inspection_5_water_availability = Column(String)
    inspection_5_water_level = Column(Float)
    inspection_5_plant_per_sq_meter = Column(Float)
    inspection_5_estimated_yield_per_acre = Column(Float)
    inspection_5_plough_down = Column(String)
    inspection_5_disease_or_pest_attack = Column(String)
    inspection_5_if_yes_disease = Column(String)
    inspection_5_humidity = Column(Float)
    inspection_5_temperature = Column(Float)
    inspection_5_date_and_time = Column(DateTime)
    inspection_5_image = Column(String)

    inspection_6_date_of_inspection = Column(DateTime)
    inspection_6_net_acres = Column(Float)
    inspection_6_date_of_harvesting_male_ = Column(DateTime)
    inspection_6_male_h_acres = Column(Float)
    inspection_6_date_of_harvesting_female_ = Column(DateTime)
    inspection_6_female_h_acres = Column(Float)
    inspection_6_female_harvesting_authorization_given_by = Column(String)
    inspection_6_mode_of_harvest = Column(String)
    inspection_6_no_of_tanks = Column(Integer)
    inspection_6_date_of_dispatch = Column(DateTime)
    inspection_6_moisture_ = Column(Float)
    inspection_6_no_of_filled_gunny_bags = Column(Integer)
    inspection_6_stn_no = Column(String)
    inspection_6_truck_no = Column(String)
    inspection_6_quality_status = Column(String)
    inspection_6_exposed_to_rain_or_hail_storm = Column(String)
    inspection_6_total_nutrient_application = Column(Float)

    # Yield/Production data (from CSV columns)
    sum_of_received_raw__qty = Column(Float)  # Note: double underscore in database
    packed_qty = Column(Float)
    productivity_of_packed_seed = Column(Float)
    slab = Column(String)
    pos_done_by = Column(String)
    production_manager = Column(String)
    production_plant = Column(String)
    production_location = Column(String)
    production_code = Column(String)
    po_soaking_acres = Column(Float)
    purchase_order = Column(String)
    planting_list_soaking_acres = Column(Float)
    net_acerage_area = Column(Float)
    tp_days = Column(Integer)
    tp_days_slab = Column(String)
    amount_inr = Column(Float)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SupplyChainPlanning(Base):
    __tablename__ = "supply_chain_planning"
    __table_args__ = {"schema": "operations"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_revision_version = Column(String(50))
    season = Column(String(50))
    season_id = Column(String(20))  # Changed to String to match database type
    crop = Column(String(100))
    crop_id = Column(String(20))    # Changed to String to match CropRecord.crop_id type
    variety = Column(String(100))
    variety_id = Column(String(20)) # Changed to String to match VarietyRecord.variety_id type
    village = Column(String(100))
    location_id = Column(String(20))  # Changed to String to match LocationRecord.location_id type
    location = Column(String(100))  # added for location name (may be same as village or different)
    grower = Column(String(100))
    state = Column(String(100))  # added for state-based planning
    hybrid = Column(String(100))  # added for hybrid type
    mandal = Column(String(100))  # added for mandal
    district = Column(String(100))  # added for district
    net_acres_current = Column(Numeric(10, 2))
    productivity = Column(Numeric(10, 2))
    actual_productivity = Column(Numeric(10, 2))
    production_allocation = Column(Numeric(10, 2))
    actual_net_acres = Column(Numeric(10, 2))
    adjusted_production_allocation = Column(Numeric(10, 2))
    estimated_cost_per_kg = Column(Numeric(10, 2))
    estimated_production_cost = Column(Numeric(12, 2))
    actual_received_qty  = Column(Numeric(12, 2))
    actual_amount  = Column(Numeric(14, 2))
    actual_packaged_qty  = Column(Numeric(12, 2))

    category_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SeasonCropInspectionBase(Base):
    __tablename__ = "season_crop_inspection_base"
    __table_args__ = {'schema': 'operations'}

    id = Column(Integer, primary_key=True, index=True)

    season_id = Column(String(20), nullable=True)
    crop_id = Column(String(20), ForeignKey("operations.crops.crop_id"), nullable=True)
    variety_id = Column(String(20), ForeignKey("operations.varieties.variety_id"), nullable=True)
    location_id = Column(String(20), ForeignKey("operations.locations.location_id"), nullable=True)
    grower_id = Column(String(20), ForeignKey("operations.growers.grower_id"), nullable=True)
    organizer_id = Column(String(20), ForeignKey("operations.organizers.organizer_id"))
    lot_id = Column(String(50), nullable=True)

    hybrid_id = Column(String(50), nullable=True)
    organizer_name = Column(String(100), nullable=True)
    grower_name = Column(String(100), nullable=True)
    grower_gender = Column(String(10), nullable=True)
    purchasing_document_number = Column(Float, nullable=True)
    fathers_name = Column(String(100), nullable=True)
    # Add these to the SQLAlchemy model class
    production_code = Column(String(100), nullable=True)
    production_location = Column(String(100), nullable=True)
    village = Column(String(100), nullable=True)
    mandal = Column(String(100), nullable=True)
    mandal_id = Column(String(20), nullable=True)
    district = Column(String(100), nullable=True)
    district_id = Column(String(20), nullable=True)
    state = Column(String(100), nullable=True)

    male_parent_seed_lot_no = Column(String(50), nullable=True)
    male_soaking_acre = Column(Float, nullable=True)
    male_no_of_pkt = Column(Float, nullable=True)
    male_qty_in_kgs = Column(Float, nullable=True)

    female_parent_seed_lot_no = Column(String(50), nullable=True)
    female_soaking_acre = Column(Float, nullable=True)
    female_no_of_pkt = Column(Float, nullable=True)
    female_qty_in_kgs = Column(Float, nullable=True)

    production_officer = Column(String(100), nullable=True)
    tfa_name = Column(String(100), nullable=True)
    production_plant = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    # category = relationship("CategoryRecord", back_populates="inspections")
    crop_record = relationship(
        "CropRecord",
        back_populates="inspections",
        foreign_keys=[crop_id],
    )
    variety = relationship("VarietyRecord", back_populates="inspections", foreign_keys=[variety_id])
    location = relationship("LocationRecord", back_populates="inspections", foreign_keys=[location_id])
    grower = relationship("GrowerRecord", back_populates="inspections")
    organizer = relationship("OrganizerRecord", back_populates="inspections")


class YieldRecord(Base):
    __tablename__ = "season_crop_yield"
    __table_args__ = {'schema': 'operations'}

    id = Column(Integer, primary_key=True, index=True)

    # Foreign Key Identifiers
    season_id = Column(String(50), nullable=True)
    crop_id = Column(String(50),nullable=True)
    variety_id = Column(String(50),  nullable=True)
    grower_id = Column(String(50), nullable=True)
    location_id = Column(String(50), nullable=True)
    lot_id = Column(String(100), nullable=True)

    # Yield-specific fields
    physical_received_qty_as_per_sap = Column(Float, nullable=True)
    m1_soaking_date = Column(Date, nullable=True)
    m1_soaking_slab = Column(String(100), nullable=True)
    female_soaking_date = Column(Date, nullable=True)
    female_tp_date = Column(Date, nullable=True)

    sowing_acres = Column(Float, nullable=True)
    net_tp_acres = Column(Float, nullable=True)
    net_acerage_area = Column(Float, nullable=True)
    final_harvestable_area = Column(Float, nullable=True)

    sum_of_received_raw_qty = Column(Float, nullable=True)
    qty = Column(Float, nullable=True)
    rate_per_kg = Column(Float, nullable=True)
    amount_inr = Column(Float, nullable=True)

    productivity_of_packed_seed = Column(Float, nullable=True)
    packed_qt = Column(Float, nullable=True)
    productvity = Column(Float, nullable=True)

    slab = Column(String(50), nullable=True)
    pos_done_b = Column(String(100), nullable=True)
    production_manager = Column(String(100), nullable=True)
    production_plant = Column(String(100), nullable=True)
    production_location = Column(String(100), nullable=True)
    production_co = Column(String(100), nullable=True)

    po_soaking_acres = Column(Float, nullable=True)
    purchase_order = Column(String(100), nullable=True)
    planting_list_soaking_acres = Column(Float, nullable=True)
    net_acres = Column(Float, nullable=True)

    tp_days = Column(Integer, nullable=True)
    tp_days_slab = Column(String(50), nullable=True)

    # Audit fields
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    # crop = relationship("CropRecord", back_populates="yields")
    # variety = relationship("VarietyRecord", back_populates="yields")
    # location = relationship("LocationRecord", back_populates="yields")
    # grower = relationship("GrowerRecord", back_populates="yields")
    # organizer = relationship("OrganizerRecord", back_populates="yields")

    # def __repr__(self):
    #     return f"<YieldRecord(grower_id={self.grower_id}, crop_id={self.crop_id}, lot_id={self.lot_id})>"


class YieldInspectionView(Base):
    __tablename__ = "yield_inspection_view"
    __table_args__ = {'schema': 'operations'}

    crop_id = Column(String, primary_key=True)
    crop_name = Column(String)
    season_id = Column(String, primary_key=True)
    season_name = Column(String)
    variety_id = Column(String, primary_key=True)
    variety_name = Column(String)
    village = Column(String)
    grower_name = Column(String)
    lot_id = Column(String)
    grower_id = Column(String)
    net_acres = Column(Float)
    physical_received_qty = Column(Float)
    packed_qty = Column(Float)
    productivity = Column(Float)
    stage_forecast1 = Column(Float)
    stage_forecast2 = Column(Float)

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class SeasonRecord(Base):
    __tablename__ = "seasons"
    __table_args__ = {"schema": "operations"}

    season_id = Column(String, primary_key=True)
    season_name = Column(String)

class SeedForecast(Base):
    __tablename__ = "seed_forecast"
    __table_args__ = {"schema": "operations"}  # schema specified

    grower_id = Column(String, primary_key=True)
    crop_id = Column(String, primary_key=True)
    lot_id = Column(String, primary_key=True)
    season_id = Column(String, primary_key=True)
    variety_id = Column(String, primary_key=True)
    stage_forecast1 = Column(Float)
    stage_forecast2 = Column(Float)
    stage_forecast3 = Column(Float)
    stage_forecast4 = Column(Float)
    stage_forecast5 = Column(Float)
    stage_forecast6 = Column(Float)



# Create tables
# CategoryRecord.__table__.create(bind=engine, checkfirst=True)
# ColumnMetadataRecord.__table__.create(bind=engine, checkfirst=True)
# CropRecord.__table__.create(bind=engine, checkfirst=True)
# VarietyRecord.__table__.create(bind=engine, checkfirst=True)
# LocationRecord.__table__.create(bind=engine, checkfirst=True)
# GrowerRecord.__table__.create(bind=engine, checkfirst=True)
# OrganizerRecord.__table__.create(bind=engine, checkfirst=True)
# SeasonCropInspectionBase.__table__.create(bind=engine, checkfirst=True)
YieldRecord.__table__.create(bind=engine, checkfirst=True)