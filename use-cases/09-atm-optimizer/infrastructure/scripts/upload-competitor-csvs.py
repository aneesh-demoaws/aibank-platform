#!/usr/bin/env python3
"""
Upload updated competitor CSV files to S3.

Replaces the old CSV data (with real bank names) with the new
colour-aliased data (Red Bank, Gold Bank, etc.).

Run this BEFORE step1_parquet.py to update the Athena tables.

Usage:
    python3 infrastructure/scripts/upload-competitor-csvs.py
"""

import boto3
import os

REGION = "me-south-1"
BUCKET = os.environ.get("ATM_S3_DATA_BUCKET", "atm-optimizer-data-me-south-1")

s3 = boto3.client("s3", region_name=REGION)

FILES = {
    "data/competitor_atm_locations.csv": "competitor_atm_locations/competitor_atm_locations.csv",
    "data/competitor_proximity.csv": "competitor_proximity/competitor_proximity.csv",
}


def main():
    print("=" * 60)
    print("  Upload updated competitor CSVs to S3")
    print(f"  Bucket: {BUCKET}")
    print("=" * 60)

    for local_path, s3_key in FILES.items():
        full_path = os.path.join(os.getcwd(), local_path)
        if not os.path.exists(full_path):
            print(f"  ❌ File not found: {full_path}")
            continue

        # Delete existing objects in the S3 prefix (folder)
        prefix = s3_key.rsplit("/", 1)[0] + "/"
        print(f"\n  Clearing s3://{BUCKET}/{prefix} ...")
        resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
        for obj in resp.get("Contents", []):
            s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
            print(f"    Deleted: {obj['Key']}")

        # Upload new CSV
        print(f"  Uploading {local_path} → s3://{BUCKET}/{s3_key}")
        s3.upload_file(full_path, BUCKET, s3_key)
        print(f"  ✅ Uploaded")

    print(f"\n{'='*60}")
    print("  Done. Now run:")
    print("    python3 infrastructure/scripts/step1_parquet.py")
    print("    python3 infrastructure/scripts/step2_competition_index.py")
    print("    python3 infrastructure/scripts/deploy-streamlit-agentcore.py")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
