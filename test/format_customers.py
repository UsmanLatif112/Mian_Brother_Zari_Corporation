"""Convert CUSTOMERS.xlsx into formatted CSV (name, phone, type, address, old book no, amount)."""

import re
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
SRC = BASE / "CUSTOMERS.xlsx"


def is_code(token: str) -> bool:
    """Address / old-book codes start with a digit (e.g. 112, 112/E.B, 28/11.L)."""
    return bool(token) and token[0].isdigit()


def parse_customer_name(raw: str):
    if not isinstance(raw, str) or not raw.strip():
        return "", "", ""
    tokens = raw.strip().split()
    codes = []
    i = len(tokens) - 1
    while i >= 0 and is_code(tokens[i]) and len(codes) < 2:
        codes.append(tokens[i])
        i -= 1
    codes.reverse()  # [address] or [address, old_book]
    name = " ".join(tokens[: i + 1]).strip()
    address = ""
    old_book = ""
    if len(codes) == 1:
        address = codes[0]
    elif len(codes) >= 2:
        address = codes[0]
        old_book = codes[1]
    return name, address, old_book


def format_phone(tel) -> str:
    if pd.isna(tel):
        return "0000000000"
    try:
        s = str(int(float(tel)))
    except (ValueError, OverflowError):
        s = re.sub(r"\D", "", str(tel))
    return s if s else "0000000000"


def format_type(t) -> str:
    if pd.isna(t):
        return "good"
    s = str(t).strip()
    return s if s else "good"


def format_amount(bal) -> str:
    if pd.isna(bal):
        return ""
    if isinstance(bal, str):
        bal = bal.replace("$", "").replace(",", "").strip()
        try:
            bal = float(bal)
        except ValueError:
            return str(bal).replace("$", "")
    if float(bal) == int(bal):
        return str(int(bal))
    return str(bal)


def convert(limit: int | None = 50) -> Path:
    df = pd.read_excel(SRC)
    if limit is not None:
        df = df.head(limit)

    rows = []
    for _, row in df.iterrows():
        name, address, old_book = parse_customer_name(row["Customer Name"])
        rows.append(
            {
                "name": name,
                "phone": format_phone(row["Telephone 1"]),
                "type": format_type(row["Type"]),
                "address": address,
                "old book no": old_book,
                "amount": format_amount(row["Balance"]),
            }
        )

    out = pd.DataFrame(
        rows,
        columns=["name", "phone", "type", "address", "old book no", "amount"],
    )
    suffix = f"first_{limit}" if limit is not None else "all"
    out_path = BASE / f"customers_{suffix}.csv"
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


if __name__ == "__main__":
    path = convert(limit=50)
    preview = pd.read_csv(path)
    print(f"Wrote {len(preview)} rows -> {path}")
    print(preview.to_string(index=False))
