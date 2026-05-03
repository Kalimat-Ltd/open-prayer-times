import argparse
import datetime
import json

from src.app.presentation.city_day_service import calculate_city_day


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open Prayer Times — calculate prayer times for a city"
    )
    parser.add_argument("--city", required=True, help="Location name from resources/locations.csv")
    parser.add_argument(
        "--date", required=True, help="Target date in YYYY-MM-DD format"
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    target_date = datetime.date.fromisoformat(args.date)
    result = calculate_city_day(args.city, target_date)
    output = {
        "city": args.city,
        "date": args.date,
        "times": result.times,
        "method_used": result.method_used,
        "error": result.error,
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
