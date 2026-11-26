"""JSON-схема ответа из Pydantic-модели в консервативном виде, который принимают провайдеры.

$ref разворачиваются, служебные ключи (title, default) удаляются, все поля объявляются обязательными —
так модель заполняет каждое поле (хотя бы пустым значением), а Pydantic остаётся снисходительным.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache

from pydantic import BaseModel

_DROP_KEYS = {"title", "default", "examples"}


@lru_cache(maxsize=64)
def _schema_json(model: type[BaseModel]) -> str:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = copy.deepcopy(defs[node["$ref"].split("/")[-1]])
                for key, value in node.items():
                    if key != "$ref":
                        target[key] = value
                return resolve(target)
            if "allOf" in node and len(node["allOf"]) == 1:
                merged = resolve(node["allOf"][0])
                for key, value in node.items():
                    if key != "allOf":
                        merged[key] = resolve(value)
                return merged
            out = {}
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    # имена полей не служебные ключи: поле «title» должно остаться в схеме
                    out[key] = {name: resolve(sub) for name, sub in value.items()}
                elif key not in _DROP_KEYS:
                    out[key] = resolve(value)
            if out.get("type") == "object" and "properties" in out:
                out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return json.dumps(resolve(schema), ensure_ascii=False, sort_keys=False)


def json_schema(model: type[BaseModel]) -> dict:
    return json.loads(_schema_json(model))


def schema_text(model: type[BaseModel]) -> str:
    return _schema_json(model)
