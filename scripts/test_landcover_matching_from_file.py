import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from landcover_matching import match_field_to_subtype


def main():
    input_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "test_olive_pure_field.geojson",
    )

    with open(input_path, "r", encoding="utf-8") as f:
        geojson = json.load(f)

    result = match_field_to_subtype(geojson)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()