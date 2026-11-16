#!/usr/bin/env python3
"""极简 YAML 读取（零依赖）：支持注释、``key: value``、行内列表、一层块列表、引号、布尔/数字。

支持两种用法（均为零依赖，避免给云端引入 PyYAML）：

1. 扁平配置（``Config.yaml``）：``key: value``；
2. 一层块列表（``SeedStockList.yaml``）::

       Count: 3194
       Stocks:
         - Code: "000001"
           Name: 平安银行
           ListDate: "1991-04-03"
"""


def stripComment(text):
    """去掉行内注释（引号内的 # 保留）。"""
    quote = None
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "#":
            return text[:index]
    return text


def parseValue(text):
    """把 YAML 标量文本转换为 Python 值。"""
    value = stripComment(text).strip()
    if not value:
        return None
    if value in ("null", "Null", "NULL", "~"):
        return None
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [parseValue(item) for item in inner.split(",")] if inner else []
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def loads(text):
    """解析 YAML 文本为 dict；支持扁平键值与一层块列表。"""
    data, listKey, item = {}, None, None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if line.startswith("- "):                        # 列表项
            body = line[2:]
            if listKey is None:
                continue
            if ":" in body and not body.startswith(("'", '"')):
                key, _, value = body.partition(":")
                item = {key.strip(): parseValue(value)}
                data[listKey].append(item)
            else:
                item = None
                data[listKey].append(parseValue(body))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if indent > 0 and item is not None and listKey is not None:   # 列表项的续行字段
            item[key] = parseValue(value)
            continue
        listKey, item = None, None
        if value.strip() == "":
            data[key] = []
            listKey = key
        else:
            data[key] = parseValue(value)
    return data


def load(path):
    with open(path, encoding="utf-8") as file:
        return loads(file.read())
