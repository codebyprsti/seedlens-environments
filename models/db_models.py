from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Float, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import create_engine



Base = declarative_base()
engine = create_engine("postgresql://apps:PAI_Uat1_Apps@prstiai-client-dev-db-instance.cl6gqami6ntb.ap-south-1.rds.amazonaws.com:5432/SeedWorksDB")


class CategoryRecord(Base):
    __tablename__ = "category_records"
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
    inspections = relationship("SeasonCropInspectionBase", back_populates="category")
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
    category_id = Column(Integer, ForeignKey("operations.category_records.category_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="column_metadata")


class CropRecord(Base):
    __tablename__ = "crop_records"
    __table_args__ = {'schema': 'operations'}

    crop_id: Mapped[str] = mapped_column(String(20), primary_key=True, unique=True, index=True)
    crop_name: Mapped[str] = mapped_column(String(100), nullable=False)
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("operations.category_records.category_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="crops")
    varieties = relationship("VarietyRecord", back_populates="crop")
    inspections = relationship("SeasonCropInspectionBase", back_populates="crop")


class VarietyRecord(Base):
    __tablename__ = "variety_records"
    __table_args__ = {'schema': 'operations'}

    variety_id = Column(String(20), primary_key=True, unique=True, index=True, nullable=False)
    variety_name = Column(String(100), nullable=False)
    crop_id = Column(String(20), ForeignKey("operations.crop_records.crop_id"), nullable=False)
    category_id = Column(Integer, ForeignKey("operations.category_records.category_id"), nullable=False)
    male_parent_seed_lot_no = Column(String(50))
    female_parent_seed_lot_no = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="varieties")
    crop = relationship("CropRecord", back_populates="varieties")
    inspections = relationship("SeasonCropInspectionBase", back_populates="variety")


class LocationRecord(Base):
    __tablename__ = "location_records"
    __table_args__ = {'schema': 'operations'}

    location_id = Column(String(20), unique=True, primary_key=True, index=True, nullable=False)
    village = Column(String(100), nullable=False)
    mandal = Column(String(100))
    mandal_id = Column(Integer)
    district = Column(String(100))
    district_id = Column(Integer)
    state = Column(String(100))
    category_id = Column(Integer, ForeignKey("operations.category_records.category_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="locations")
    inspections = relationship("SeasonCropInspectionBase", back_populates="location")


class GrowerRecord(Base):
    __tablename__ = "grower_records"
    __table_args__ = {'schema': 'operations'}

    grower_id = Column(String(20), unique=True, primary_key=True, index=True, nullable=False)
    grower_name = Column(String(100), nullable=False)
    fathers_name = Column(String(100))
    grower_gender = Column(String(10))
    category_id = Column(Integer, ForeignKey("operations.category_records.category_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="growers")
    inspections = relationship("SeasonCropInspectionBase", back_populates="grower")


class OrganizerRecord(Base):
    __tablename__ = "organizer_records"
    __table_args__ = {'schema': 'operations'}

    organizer_id = Column(String(20), unique=True, primary_key=True, index=True, nullable=False)
    organizer_name = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey("operations.category_records.category_id"), nullable=False)
    production_plant = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    category = relationship("CategoryRecord", back_populates="organizers")
    inspections = relationship("SeasonCropInspectionBase", back_populates="organizer")


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

    yield_physical_received_qty_as_per_sap = Column(Float)
    yield_packed_qty = Column(Float)
    yield_productvity = Column(Float)
    yield_yield_slab = Column(String)
    yield_pos_done_by = Column(String)
    yield_production_manager = Column(String)
    yield_production_plant = Column(String)
    yield_production_location = Column(String)
    yield_production_code = Column(String)
    yield_po_soaking_acres = Column(Float)
    yield_purchase_order = Column(String)
    yield_planting_list_soaking_acres = Column(Float)
    yield_net_acres = Column(Float)
    yield_tp_days = Column(Integer)
    yield_tp_days_slab = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SeasonCropInspectionBase(Base):
    __tablename__ = "season_crop_inspection_base"
    __table_args__ = {'schema': 'operations'}

    id = Column(Integer, primary_key=True, index=True)

    season_id = Column(String(20), nullable=False)
    crop_id = Column(String(20), ForeignKey("operations.crop_records.crop_id"), nullable=False)
    variety_id = Column(String(20), ForeignKey("operations.variety_records.variety_id"), nullable=False)
    location_id = Column(String(20), ForeignKey("operations.location_records.location_id"), nullable=False)
    grower_id = Column(String(20), ForeignKey("operations.grower_records.grower_id"), nullable=False)
    organizer_id = Column(String(20), ForeignKey("operations.organizer_records.organizer_id"))
    category_id = Column(Integer, ForeignKey("operations.category_records.category_id"), nullable=False)
    lot_id = Column(String(50), nullable=True)

    hybrid_id = Column(String(50), nullable=True)
    organizer_name = Column(String(100), nullable=True)
    grower_name = Column(String(100), nullable=True)
    grower_gender = Column(String(10), nullable=True)
    purchasing_document_number = Column(String(50), nullable=True)
    fathers_name = Column(String(100), nullable=True)
    village = Column(String(100), nullable=True)
    mandal = Column(String(100), nullable=True)
    taluka_id = Column(String(20), nullable=True)
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
    category = relationship("CategoryRecord", back_populates="inspections")
    crop = relationship("CropRecord", back_populates="inspections")
    variety = relationship("VarietyRecord", back_populates="inspections")
    location = relationship("LocationRecord", back_populates="inspections")
    grower = relationship("GrowerRecord", back_populates="inspections")
    organizer = relationship("OrganizerRecord", back_populates="inspections")


# Create tables
# CategoryRecord.__table__.create(bind=engine, checkfirst=True)
# ColumnMetadataRecord.__table__.create(bind=engine, checkfirst=True)
# CropRecord.__table__.create(bind=engine, checkfirst=True)
# VarietyRecord.__table__.create(bind=engine, checkfirst=True)
# LocationRecord.__table__.create(bind=engine, checkfirst=True)
# GrowerRecord.__table__.create(bind=engine, checkfirst=True)
# OrganizerRecord.__table__.create(bind=engine, checkfirst=True)
# SeasonCropInspectionBase.__table__.create(bind=engine, checkfirst=True)