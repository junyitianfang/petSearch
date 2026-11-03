"""导入加盟门店名单到 SQLite（与高德地图检索无关，平台自己的数据）。

用法:
    python import_stores.py 门店名单.xlsx           # 全量替换现有名单
    python import_stores.py 门店名单.xlsx --append  # 追加，不清空原有数据

支持 .xlsx 和 .csv（Excel 另存的 CSV 也可以）。列头自动模糊匹配：
    门店名: 门店名/门店名称/名称/店名/名字
    门店类型: 门店类型/类型
    地址: 地址/详细地址/门店地址
"""

import argparse
import csv
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "petserch.db")

NAME_KEYS = {"门店名", "门店名称", "名称", "店名", "名字"}
TYPE_KEYS = {"门店类型", "类型"}
ADDR_KEYS = {"地址", "详细地址", "门店地址"}


def read_rows(path):
    """读出每行数据，返回 [{列头: 值}]。"""
    if path.lower().endswith(".csv"):
        raw = open(path, "rb").read()
        for enc in ("utf-8-sig", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise SystemExit("无法识别 CSV 编码（试过 utf-8 和 gbk）")
        return list(csv.DictReader(text.splitlines()))

    import openpyxl  # venv 里 pip install openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    out = []
    for r in rows[1:]:
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        out.append(
            {
                header[i]: ("" if i >= len(r) or r[i] is None else str(r[i]).strip())
                for i in range(len(header))
            }
        )
    return out


def pick(row, keys):
    for col, val in row.items():
        if col.strip() in keys and str(val).strip():
            return str(val).strip()
    return ""


def main():
    parser = argparse.ArgumentParser(description="导入加盟门店名单")
    parser.add_argument("file", help="xlsx 或 csv 文件路径")
    parser.add_argument("--append", action="store_true", help="追加而不是全量替换")
    args = parser.parse_args()

    rows = read_rows(args.file)
    stores = []
    skipped = 0
    for r in rows:
        name = pick(r, NAME_KEYS)
        if not name:
            skipped += 1
            continue
        stores.append(
            (
                name,
                pick(r, TYPE_KEYS),
                pick(r, ADDR_KEYS),
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
        )

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS partner_store (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT DEFAULT '',
            address TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    if not args.append:
        conn.execute("DELETE FROM partner_store")
    conn.executemany(
        "INSERT INTO partner_store (name, type, address, created_at) VALUES (?,?,?,?)",
        stores,
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM partner_store").fetchone()[0]
    conn.close()

    mode = "追加" if args.append else "全量替换"
    suffix = f"，跳过 {skipped} 行无名称数据" if skipped else ""
    print(f"导入 {len(stores)} 家门店（{mode}{suffix}）")
    print(f"数据库现共有 {total} 家加盟门店")


if __name__ == "__main__":
    main()
