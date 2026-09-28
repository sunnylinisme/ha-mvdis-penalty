"""Extract the one model and charset needed from a verified ddddocr wheel."""

from __future__ import annotations

import ast
import json
import sys
import zipfile
from pathlib import Path


def main() -> None:
    wheel = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(wheel) as archive:
        model = archive.read("ddddocr/common_old.onnx")
        source = archive.read("ddddocr/models/charset_manager.py").decode("utf-8")

    tree = ast.parse(source)
    charset: list[str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_get_old_charset":
            returned = next(
                (child for child in ast.walk(node) if isinstance(child, ast.Return)),
                None,
            )
            if returned is not None:
                value = ast.literal_eval(returned.value)
                if isinstance(value, list) and all(
                    isinstance(item, str) for item in value
                ):
                    charset = value
            break

    if not charset or charset[0] != "":
        raise RuntimeError("Could not extract the ddddocr charset")

    (destination / "common_old.onnx").write_bytes(model)
    (destination / "charset.json").write_text(
        json.dumps(charset, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
