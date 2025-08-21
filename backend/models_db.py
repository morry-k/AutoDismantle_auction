from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from db import Base

class AuctionSheet(Base):
    __tablename__ = "auction_sheets"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String, nullable=False)
    auction_name = Column(String, nullable=True)
    auction_date = Column(Date, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    vehicles = relationship("Vehicle", back_populates="sheet", cascade="all, delete-orphan")

class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, index=True)
    sheet_id = Column(Integer, ForeignKey("auction_sheets.id", ondelete="CASCADE"), index=True, nullable=False)
    auction_no = Column(String, nullable=True)
    maker = Column(String, nullable=True)
    car_name = Column(String, nullable=True)
    grade = Column(String, nullable=True)
    model_code = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    mileage_km = Column(Integer, nullable=True)
    color = Column(String, nullable=True)
    shift = Column(String, nullable=True)
    inspection_until = Column(String, nullable=True)
    score = Column(String, nullable=True)
    start_price_yen = Column(Integer, nullable=True)
    raw_extracted_json = Column(JSON, nullable=True)
    lane = Column(String, nullable=True)             # ← 追加

    sheet = relationship("AuctionSheet", back_populates="vehicles")
    valuations = relationship("Valuation", back_populates="vehicle", cascade="all, delete-orphan")

class Valuation(Base):
    __tablename__ = "valuations"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id", ondelete="CASCADE"), index=True, nullable=False)
    algo_version = Column(String, nullable=False, default="v0.1-scrap-only")
    recommended_bid_yen = Column(Integer, nullable=True)
    resource_value_yen = Column(Integer, nullable=True)
    component_value_yen = Column(Integer, nullable=True)
    assumptions_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    vehicle = relationship("Vehicle", back_populates="valuations")
