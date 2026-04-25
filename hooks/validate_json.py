#!/usr/bin/env python3
"""知识条目 JSON 校验工具。

支持单文件和多文件（含 glob 通配符）输入，校验 JSON
结构、必填字段、ID 格式、status 枚举、URL 格式等。

用法：python hooks/validate_json.py <json_file> [json_file2 ...]
"""

import json
import re
import sys
from pathlib import Path

REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES = frozenset({"draft", "review", "published", "archived"})
VALID_AUDIENCES = frozenset({"beginner", "intermediate", "advanced"})

ID_PATTERN = re.compile(
    r"^(?P<source>[a-z][a-z0-9-]+)-(?P<date>\d{8})-(?P<seq>\d{3,})$"
)

URL_PATTERN = re.compile(r"^https?://\S+$")


def validate_file(filepath: Path) -> list[str]:
    """校验单个 JSON 文件，返回错误信息列表。"""
    errors: list[str] = []
    label = str(filepath)

    # ---- 解析 JSON ----
    try:
        with filepath.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return [f"{label}: JSON 解析失败 — {exc}"]
    except OSError as exc:
        return [f"{label}: 文件读取失败 — {exc}"]

    if not isinstance(data, dict):
        return [f"{label}: 根元素应为 JSON 对象，实际为 {type(data).__name__}"]

    # ---- 必填字段存在性与类型 ----
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            errors.append(f"{label}: 缺少必填字段 `{field}`")
            continue
        value = data[field]
        if not isinstance(value, expected_type):
            errors.append(
                f"{label}: `{field}` 类型错误，期望 {expected_type.__name__}，"
                f"实际 {type(value).__name__}"
            )

    if errors:
        return errors

    # ---- ID 格式：{source}-{YYYYMMDD}-{NNN} ----
    m = ID_PATTERN.match(data["id"])
    if not m:
        errors.append(
            f"{label}: `id` 格式不符合 `{{source}}-{{YYYYMMDD}}-{{NNN}}`，"
            f"当前值: {data['id']!r}"
        )
    else:
        if data.get("source") and m.group("source") != data["source"]:
            errors.append(
                f"{label}: `id` 中 source=`{m.group('source')}` "
                f"与字段 source=`{data['source']}` 不一致"
            )

    # ---- status 枚举 ----
    if data["status"] not in VALID_STATUSES:
        errors.append(
            f"{label}: `status` 非法值 {data['status']!r}，"
            f"允许: {sorted(VALID_STATUSES)}"
        )

    # ---- URL 格式 ----
    if not URL_PATTERN.match(data["source_url"]):
        errors.append(
            f"{label}: `source_url` 格式不合法，"
            f"当前值: {data['source_url']!r}"
        )

    # ---- 摘要至少 20 字 ----
    if len(data["summary"]) < 20:
        errors.append(
            f"{label}: `summary` 长度不足 ({len(data['summary'])} 字)，至少需要 20 字"
        )

    # ---- 标签至少 1 个 ----
    if len(data["tags"]) < 1:
        errors.append(f"{label}: `tags` 至少包含 1 个标签")

    # 检查标签是否均为字符串
    for i, tag in enumerate(data["tags"]):
        if not isinstance(tag, str):
            errors.append(
                f"{label}: `tags[{i}]` 类型错误，期望 str，实际 {type(tag).__name__}"
            )

    # ---- 可选字段：score (1-10) ----
    if "score" in data:
        score = data["score"]
        if not isinstance(score, (int, float)) or score < 1 or score > 10:
            errors.append(
                f"{label}: `score` 应在 1-10 范围，当前值: {score!r}"
            )

    # ---- 可选字段：audience ----
    if "audience" in data:
        if data["audience"] not in VALID_AUDIENCES:
            errors.append(
                f"{label}: `audience` 非法值 {data['audience']!r}，"
                f"允许: {sorted(VALID_AUDIENCES)}"
            )

    return errors


def collect_files(args: list[str]) -> list[Path]:
    """将命令行参数展开为文件路径列表（支持 glob 通配符）。"""
    files: list[Path] = []
    for arg in args:
        path = Path(arg)
        if "*" in arg or "?" in arg or "[" in arg:
            # glob 通配符
            matches = list(path.parent.glob(path.name)) if path.is_absolute() else list(Path().glob(arg))
            if not matches:
                print(f"WARNING: 通配符 `{arg}` 未匹配到任何文件", file=sys.stderr)
            files.extend(sorted(matches))
        else:
            files.append(path)
    return files


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "用法: python hooks/validate_json.py <json_file> [json_file2 ...]",
            file=sys.stderr,
        )
        return 1

    files = collect_files(sys.argv[1:])
    if not files:
        print("ERROR: 未找到任何 JSON 文件", file=sys.stderr)
        return 1

    total = len(files)
    passed = 0
    failed_files: list[str] = []

    for filepath in files:
        errors = validate_file(filepath)
        if errors:
            failed_files.append(str(filepath))
            for err in errors:
                print(f"  ✗ {err}")
        else:
            print(f"  ✓ {filepath}")
            passed += 1

    # ---- 汇总统计 ----
    print()
    print(f"文件总数: {total}")
    print(f"通过: {passed}")
    print(f"失败: {total - passed}")
    if failed_files:
        print(f"失败文件: {', '.join(failed_files)}")

    return 0 if not failed_files else 1


if __name__ == "__main__":
    sys.exit(main())
