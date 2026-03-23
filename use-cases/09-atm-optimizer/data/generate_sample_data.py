#!/usr/bin/env python3
"""
Generate realistic sample data for the ATM Profitability Optimizer.

Produces:
  - atm_proximity.csv      (28x28 haversine distance matrix)
  - sample_transactions.csv (6 months of transaction data)
  - sample_maintenance.csv  (maintenance cost records)
  - sample_cash_levels.csv  (daily cash level records)

All currency values are in BHD (Bahraini Dinar).
"""

import csv
import math
import os
import random
import uuid
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
EARTH_RADIUS_KM = 6371.0

# Date range: Aug 2025 – Jan 2026 (6 months)
START_DATE = date(2025, 8, 1)
END_DATE = date(2026, 1, 31)

random.seed(42)

# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in km between two GPS points."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Load ATM locations
# ---------------------------------------------------------------------------

def load_atm_locations() -> list[dict]:
    """Load ATM locations from the CSV file."""
    locations = []
    with open(os.path.join(DATA_DIR, "atm_locations.csv"), newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["latitude"] = float(row["latitude"])
            row["longitude"] = float(row["longitude"])
            row["daily_capacity"] = int(row["daily_capacity"])
            locations.append(row)
    return locations


def load_branch_locations() -> list[dict]:
    """Load branch locations from the CSV file."""
    branches = []
    with open(os.path.join(DATA_DIR, "branch_locations.csv"), newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["latitude"] = float(row["latitude"])
            row["longitude"] = float(row["longitude"])
            row["atm_count"] = int(row["atm_count"])
            row["avg_daily_footfall"] = int(row["avg_daily_footfall"])
            branches.append(row)
    return branches


# ---------------------------------------------------------------------------
# 2.3.1  Generate atm_proximity.csv
# ---------------------------------------------------------------------------

def generate_proximity(locations: list[dict]) -> None:
    """Generate 28x28 haversine distance matrix."""
    branch_ids = {loc["atm_id"]: loc.get("branch_id", "") for loc in locations}
    out_path = os.path.join(DATA_DIR, "atm_proximity.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source_atm_id", "target_atm_id", "distance_km", "is_same_branch"])
        for src in locations:
            for tgt in locations:
                dist = haversine(src["latitude"], src["longitude"],
                                 tgt["latitude"], tgt["longitude"])
                same_branch = (
                    bool(branch_ids[src["atm_id"]])
                    and branch_ids[src["atm_id"]] == branch_ids[tgt["atm_id"]]
                )
                writer.writerow([
                    src["atm_id"],
                    tgt["atm_id"],
                    round(dist, 4),
                    same_branch,
                ])
    print(f"  ✓ atm_proximity.csv  ({len(locations) * len(locations)} rows)")


# ---------------------------------------------------------------------------
# 2.3.2  Generate sample_transactions.csv
# ---------------------------------------------------------------------------

# Transaction-volume profiles by location type
# (base_daily_txns, weekend_multiplier)
LOCATION_PROFILES = {
    "mall":       (280, 1.6),   # malls busier on weekends
    "branch":     (200, 0.5),   # branches quieter on weekends
    "hospital":   (150, 0.8),   # hospitals slightly less on weekends
    "standalone": (160, 0.9),   # standalone fairly steady
    "airport":    (250, 1.3),   # airport busier on weekends/holidays
}

# Hourly weight distribution (24 hours, index 0 = midnight)
HOURLY_WEIGHTS_DEFAULT = [
    0.01, 0.005, 0.005, 0.005, 0.005, 0.01,   # 00-05
    0.02, 0.05,  0.08,  0.10,  0.10,  0.09,    # 06-11
    0.08, 0.07,  0.06,  0.06,  0.05,  0.05,    # 12-17
    0.06, 0.05,  0.04,  0.03,  0.02, 0.015,    # 18-23
]

# Hospital ATM peaks during visiting hours (10-12, 16-19)
HOURLY_WEIGHTS_HOSPITAL = [
    0.005, 0.002, 0.002, 0.002, 0.002, 0.005,  # 00-05
    0.01,  0.03,  0.05,  0.08,  0.12,  0.12,   # 06-11  (visiting hours AM)
    0.06,  0.04,  0.03,  0.04,  0.10,  0.10,   # 12-17  (visiting hours PM)
    0.08,  0.06,  0.04,  0.02,  0.01, 0.005,   # 18-23
]

TRANSACTION_TYPES = ["withdrawal", "balance_inquiry", "deposit"]
TRANSACTION_TYPE_WEIGHTS = [0.70, 0.20, 0.10]

# Amount ranges in BHD by transaction type
AMOUNT_RANGES = {
    "withdrawal":     (20.0, 500.0),
    "deposit":        (50.0, 2000.0),
    "balance_inquiry": (0.0, 0.0),
}

# Fee range in BHD (0.100 - 0.500 per transaction)
FEE_RANGE = (0.100, 0.500)


def _pick_hour(location_type: str = "standalone") -> int:
    """Weighted random hour of day, with hospital-specific visiting hour peaks."""
    weights = HOURLY_WEIGHTS_HOSPITAL if location_type == "hospital" else HOURLY_WEIGHTS_DEFAULT
    return random.choices(range(24), weights=weights, k=1)[0]


def generate_transactions(locations: list[dict]) -> None:
    """Generate 6 months of realistic transaction data."""
    out_path = os.path.join(DATA_DIR, "sample_transactions.csv")
    row_count = 0

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "transaction_id", "atm_id", "timestamp",
            "transaction_type", "amount", "fee",
        ])

        current = START_DATE
        while current <= END_DATE:
            is_weekend = current.weekday() >= 4  # Fri-Sat in Bahrain
            for loc in locations:
                profile = LOCATION_PROFILES.get(loc["location_type"], (160, 1.0))
                base, wknd_mult = profile
                daily_txns = int(base * (wknd_mult if is_weekend else 1.0))
                # Add ±15 % random noise
                daily_txns = max(1, int(daily_txns * random.uniform(0.85, 1.15)))

                for _ in range(daily_txns):
                    hour = _pick_hour(loc["location_type"])
                    minute = random.randint(0, 59)
                    second = random.randint(0, 59)
                    ts = datetime(
                        current.year, current.month, current.day,
                        hour, minute, second,
                    )
                    txn_type = random.choices(
                        TRANSACTION_TYPES, weights=TRANSACTION_TYPE_WEIGHTS, k=1
                    )[0]
                    lo, hi = AMOUNT_RANGES[txn_type]
                    amount = round(random.uniform(lo, hi), 3) if hi > 0 else 0.0
                    fee = round(random.uniform(*FEE_RANGE), 3)

                    writer.writerow([
                        uuid.uuid4().hex[:16],
                        loc["atm_id"],
                        ts.isoformat(),
                        txn_type,
                        f"{amount:.3f}",
                        f"{fee:.3f}",
                    ])
                    row_count += 1

            current += timedelta(days=1)

    print(f"  ✓ sample_transactions.csv  ({row_count:,} rows)")


# ---------------------------------------------------------------------------
# 2.3.3  Generate sample_maintenance.csv
# ---------------------------------------------------------------------------

MAINTENANCE_TYPES = {
    "preventive":  {"freq_days": 30, "cost_range": (15.0, 50.0),  "downtime_range": (1.0, 3.0)},
    "corrective":  {"freq_days": 90, "cost_range": (50.0, 200.0), "downtime_range": (2.0, 8.0)},
    "emergency":   {"freq_days": 180, "cost_range": (100.0, 500.0), "downtime_range": (4.0, 24.0)},
}


def generate_maintenance(locations: list[dict]) -> None:
    """Generate maintenance cost records for each ATM."""
    out_path = os.path.join(DATA_DIR, "sample_maintenance.csv")
    row_count = 0

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["atm_id", "date", "maintenance_type", "cost", "downtime_hours"])

        for loc in locations:
            for mtype, params in MAINTENANCE_TYPES.items():
                # Schedule events roughly every freq_days ± 30 %
                current = START_DATE + timedelta(days=random.randint(0, params["freq_days"]))
                while current <= END_DATE:
                    cost = round(random.uniform(*params["cost_range"]), 3)
                    downtime = round(random.uniform(*params["downtime_range"]), 1)
                    writer.writerow([
                        loc["atm_id"],
                        current.isoformat(),
                        mtype,
                        f"{cost:.3f}",
                        downtime,
                    ])
                    row_count += 1
                    gap = int(params["freq_days"] * random.uniform(0.7, 1.3))
                    current += timedelta(days=max(1, gap))

    print(f"  ✓ sample_maintenance.csv  ({row_count:,} rows)")


# ---------------------------------------------------------------------------
# 2.3.4  Generate sample_cash_levels.csv
# ---------------------------------------------------------------------------

def generate_cash_levels(locations: list[dict]) -> None:
    """Generate daily cash level records for each ATM."""
    out_path = os.path.join(DATA_DIR, "sample_cash_levels.csv")
    row_count = 0

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "atm_id", "date", "opening_balance", "closing_balance",
            "total_withdrawals", "replenishment_amount", "replenishment_cost",
        ])

        for loc in locations:
            # Initial balance proportional to capacity
            capacity = loc["daily_capacity"]
            balance = round(capacity * random.uniform(30, 50), 3)  # BHD
            replenish_threshold = capacity * 10  # trigger replenishment

            current = START_DATE
            while current <= END_DATE:
                opening = balance
                is_weekend = current.weekday() >= 4
                profile = LOCATION_PROFILES.get(loc["location_type"], (160, 1.0))
                base, wknd_mult = profile
                daily_txns = int(base * (wknd_mult if is_weekend else 1.0))
                daily_txns = max(1, int(daily_txns * random.uniform(0.85, 1.15)))

                # Average withdrawal ~50 BHD
                total_withdrawals = round(daily_txns * 0.70 * random.uniform(40, 60), 3)
                balance -= total_withdrawals

                replenishment = 0.0
                replenishment_cost = 0.0
                if balance < replenish_threshold:
                    replenishment = round(capacity * random.uniform(40, 60), 3)
                    replenishment_cost = round(random.uniform(15, 45), 3)
                    balance += replenishment

                closing = round(balance, 3)
                writer.writerow([
                    loc["atm_id"],
                    current.isoformat(),
                    f"{opening:.3f}",
                    f"{closing:.3f}",
                    f"{total_withdrawals:.3f}",
                    f"{replenishment:.3f}",
                    f"{replenishment_cost:.3f}",
                ])
                row_count += 1
                balance = closing
                current += timedelta(days=1)

    print(f"  ✓ sample_cash_levels.csv  ({row_count:,} rows)")


# ---------------------------------------------------------------------------
# Competitor ATM data generation
# ---------------------------------------------------------------------------

# Bank distribution: 82 total competitor ATMs (BBK/NeoBank excluded)
# Fictional colour-based names matched to real bank logo colours:
#   NBB         → Red Bank    (orange/red logo)
#   AUB         → Gold Bank   (gold & turquoise brand)
#   BisB        → Green Bank  (green Islamic banking identity)
#   Khaleeji    → Purple Bank (2024 rebrand, purple/magenta)
#   Al Salam    → Teal Bank   (teal/dark blue logo)
#   BBK         → Blue Bank   (blue brand — used when demoing to other banks)
COMPETITOR_BANKS = {
    "Red Bank": 22,
    "Gold Bank": 18,
    "Green Bank": 16,
    "Purple Bank": 13,
    "Teal Bank": 13,
}

# Governorate bounding boxes
GOVERNORATE_BOUNDS = {
    "Capital":   {"lat_min": 26.19, "lat_max": 26.27, "lon_min": 50.53, "lon_max": 50.62},
    "Muharraq":  {"lat_min": 26.24, "lat_max": 26.30, "lon_min": 50.60, "lon_max": 50.68},
    "Northern":  {"lat_min": 26.10, "lat_max": 26.24, "lon_min": 50.44, "lon_max": 50.56},
    "Southern":  {"lat_min": 25.90, "lat_max": 26.13, "lon_min": 50.45, "lon_max": 50.62},
}

# Realistic ATM name templates per bank
_COMPETITOR_NAME_TEMPLATES = {
    "Red Bank": [
        "Red Bank Seef Branch", "Red Bank Manama Main", "Red Bank Muharraq Centre",
        "Red Bank Diplomatic Area", "Red Bank Juffair", "Red Bank Riffa Branch",
        "Red Bank Isa Town", "Red Bank Sitra", "Red Bank Hamad Town", "Red Bank Budaiya",
        "Red Bank City Centre Mall", "Red Bank Bahrain Mall", "Red Bank Airport",
        "Red Bank Hoora", "Red Bank Gudaibiya", "Red Bank Adliya", "Red Bank Zinj",
        "Red Bank Tubli", "Red Bank Sanabis", "Red Bank Hidd", "Red Bank Amwaj",
        "Red Bank Bahrain Bay",
    ],
    "Gold Bank": [
        "Gold Bank City Centre", "Gold Bank Seef Tower", "Gold Bank Manama Branch",
        "Gold Bank Muharraq", "Gold Bank Diplomatic Area", "Gold Bank Juffair Mall",
        "Gold Bank Riffa", "Gold Bank Isa Town", "Gold Bank Sitra", "Gold Bank Hamad Town",
        "Gold Bank Budaiya Road", "Gold Bank Hoora", "Gold Bank Gudaibiya", "Gold Bank Adliya",
        "Gold Bank Zinj", "Gold Bank Sanabis", "Gold Bank Hidd", "Gold Bank Bahrain Bay",
    ],
    "Green Bank": [
        "Green Bank Seef Branch", "Green Bank Manama", "Green Bank Muharraq",
        "Green Bank Riffa", "Green Bank Isa Town", "Green Bank Sitra", "Green Bank Hamad Town",
        "Green Bank Budaiya", "Green Bank City Centre", "Green Bank Juffair",
        "Green Bank Hoora", "Green Bank Gudaibiya", "Green Bank Adliya", "Green Bank Zinj",
        "Green Bank Tubli", "Green Bank Sanabis",
    ],
    "Purple Bank": [
        "Purple Bank Seef", "Purple Bank Manama", "Purple Bank Muharraq",
        "Purple Bank Riffa", "Purple Bank Isa Town", "Purple Bank Sitra",
        "Purple Bank Hamad Town", "Purple Bank Budaiya", "Purple Bank Juffair",
        "Purple Bank Hoora", "Purple Bank Adliya", "Purple Bank Hidd",
        "Purple Bank Bahrain Mall",
    ],
    "Teal Bank": [
        "Teal Bank Seef", "Teal Bank Manama", "Teal Bank Muharraq",
        "Teal Bank Riffa", "Teal Bank Isa Town", "Teal Bank Sitra",
        "Teal Bank Hamad Town", "Teal Bank Budaiya", "Teal Bank Juffair",
        "Teal Bank City Centre", "Teal Bank Gudaibiya", "Teal Bank Zinj",
        "Teal Bank Tubli",
    ],
}

LOCATION_TYPES = ["branch", "mall", "standalone"]
STATUS_VALUES = ["active", "planned", "closed"]
STATUS_WEIGHTS = [0.75, 0.15, 0.10]

# Short bank codes for ID generation
_BANK_CODES = {
    "Red Bank": "RED",
    "Gold Bank": "GOLD",
    "Green Bank": "GREEN",
    "Purple Bank": "PURPLE",
    "Teal Bank": "TEAL",
}


def generate_competitor_atm_locations(neobank_locations: list[dict]) -> list[dict]:
    """Generate 82 competitor ATM records across 5 banks and 4 governorates.

    Returns list of dicts with keys:
        competitor_atm_id, bank_name, name, latitude, longitude,
        location_type, area, status
    """
    competitors: list[dict] = []
    governorates = list(GOVERNORATE_BOUNDS.keys())

    for bank_name, count in COMPETITOR_BANKS.items():
        code = _BANK_CODES[bank_name]
        names = _COMPETITOR_NAME_TEMPLATES[bank_name]

        for seq in range(1, count + 1):
            # Distribute across governorates roughly evenly
            gov = governorates[(seq - 1) % len(governorates)]
            bounds = GOVERNORATE_BOUNDS[gov]

            lat = round(random.uniform(bounds["lat_min"], bounds["lat_max"]), 4)
            lon = round(random.uniform(bounds["lon_min"], bounds["lon_max"]), 4)

            location_type = random.choice(LOCATION_TYPES)
            status = random.choices(STATUS_VALUES, weights=STATUS_WEIGHTS, k=1)[0]
            name = names[seq - 1] if seq - 1 < len(names) else f"{bank_name} ATM {seq:02d}"

            competitors.append({
                "competitor_atm_id": f"COMP_{code}_{seq:02d}",
                "bank_name": bank_name,
                "name": name,
                "latitude": lat,
                "longitude": lon,
                "location_type": location_type,
                "area": gov,
                "status": status,
            })

    # Write CSV
    out_path = os.path.join(DATA_DIR, "competitor_atm_locations.csv")
    fieldnames = [
        "competitor_atm_id", "bank_name", "name", "latitude", "longitude",
        "location_type", "area", "status",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(competitors)

    print(f"  ✓ competitor_atm_locations.csv  ({len(competitors)} rows)")
    return competitors


def generate_competitor_proximity(
    neobank_locations: list[dict],
    competitor_locations: list[dict],
) -> None:
    """Generate CSV with haversine distances between every NeoBank↔Competitor pair.

    Output columns: neobank_atm_id, competitor_atm_id, bank_name, distance_km
    """
    out_path = os.path.join(DATA_DIR, "competitor_proximity.csv")
    row_count = 0

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["neobank_atm_id", "competitor_atm_id", "bank_name", "distance_km"])

        for neo in neobank_locations:
            for comp in competitor_locations:
                dist = haversine(
                    neo["latitude"], neo["longitude"],
                    comp["latitude"], comp["longitude"],
                )
                writer.writerow([
                    neo["atm_id"],
                    comp["competitor_atm_id"],
                    comp["bank_name"],
                    round(dist, 4),
                ])
                row_count += 1

    print(f"  ✓ competitor_proximity.csv  ({row_count:,} rows)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Loading ATM locations …")
    locations = load_atm_locations()
    print(f"  Found {len(locations)} ATMs\n")

    print("Generating data files:")
    generate_proximity(locations)
    generate_transactions(locations)
    generate_maintenance(locations)
    generate_cash_levels(locations)

    # Competitor data
    competitor_locations = generate_competitor_atm_locations(locations)
    generate_competitor_proximity(locations, competitor_locations)

    print("\nDone ✓")


if __name__ == "__main__":
    main()
