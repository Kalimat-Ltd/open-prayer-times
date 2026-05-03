import argparse
import json
from pathlib import Path

from src.app.application.optimization_use_case import optimize_city_from_reference
from src.app.config import LOC_CSV_PATH, PROJECT_ROOT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open Prayer Times — run optimization for one city"
    )
    parser.add_argument("--city", required=True, help="Location name from resources/locations.csv")
    parser.add_argument(
        "--reference-file",
        required=True,
        help="Reference file path relative to project root, e.g. reference/RU/russia_kazan.txt",
    )
    parser.add_argument("--timezone-offset", required=True, type=float)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    result = optimize_city_from_reference(
        city_name=args.city,
        reference_file=PROJECT_ROOT / args.reference_file,
        loc_csv_path=Path(LOC_CSV_PATH),
        timezone_offset_hours=args.timezone_offset,
    )

    output = {
        "city": args.city,
        "reference_file": args.reference_file,
        "rmse_total": result.rmse_total,
        "mae_total": result.mae_total,
        "fajr_angle": result.fajr_angle,
        "isha_angle": result.isha_angle,
        "offsets": result.offsets,
        "convergence_info": result.convergence_info,
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
