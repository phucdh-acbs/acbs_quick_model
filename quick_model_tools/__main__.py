"""python -m quick_model_tools forecast|valuation --in input.json [--out output.json]   (- hoặc bỏ trống: stdin / stdout)
python -m quick_model_tools schemas   in định nghĩa hai công cụ cho AI"""
from __future__ import annotations

import argparse
import json
import sys

from . import forecast_tool, valuation_tool
from .schemas import tool_definitions


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quick_model_tools", description=__doc__)
    p.add_argument("tool", choices=["forecast", "valuation", "schemas"])
    p.add_argument("--in", dest="inp")
    p.add_argument("--out")
    a = p.parse_args(argv)
    if a.tool == "schemas":
        res = tool_definitions()
    else:
        text = open(a.inp, encoding="utf-8").read() if a.inp and a.inp != "-" else sys.stdin.read()
        res = {"forecast": forecast_tool.run, "valuation": valuation_tool.run}[a.tool](json.loads(text))
    out = json.dumps(res, ensure_ascii=False, indent=2) + "\n"
    if a.out and a.out != "-":
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        sys.stdout.write(out)
    return 0 if a.tool == "schemas" or res["status"] == "ok" else (10 if res["status"] == "refused" else 30)


if __name__ == "__main__":
    sys.exit(main())
