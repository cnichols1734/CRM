import csv
import re
from pathlib import Path


CSV_PATH = Path("/Users/christophernichols/PycharmProjects/CRM/csv/Traci-Hernandez-contacts.csv")


def normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    digits = re.sub(r"\D+", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def main() -> None:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)

        # Transform alt format → our fields
        column_mapping = {
            'First Name': 'first_name',
            'Last Name': 'last_name',
            'Email 1': 'email',
            'Phone Number 1': 'phone',
            'Mailing Address': 'street_address',
            'Mailing City': 'city',
            'Mailing State/Province': 'state',
            'Mailing Postal Code': 'zip_code',
            'Groups': 'groups',
        }

        transformed_rows = []
        for row in r:
            tr = {}
            for old, new in column_mapping.items():
                tr[new] = (row.get(old) or "").strip()
            transformed_rows.append(tr)

    seen_emails = set()
    seen_phones = set()
    seen_names = set()  # only when both email and phone empty

    kept = 0
    duplicates_email = 0
    duplicates_phone = 0
    duplicates_name = 0
    missing_both_names = 0
    invalid_phone_count = 0

    for idx, row in enumerate(transformed_rows, start=1):
        first = (row.get('first_name') or '').strip()
        last = (row.get('last_name') or '').strip()
        if not first and not last:
            missing_both_names += 1
            continue

        email = (row.get('email') or '').strip().lower()
        phone_norm = normalize_phone(row.get('phone') or '')
        if row.get('phone') and not phone_norm:
            invalid_phone_count += 1

        # dedupe
        if email:
            if email in seen_emails:
                duplicates_email += 1
                continue
        if phone_norm:
            if phone_norm in seen_phones:
                duplicates_phone += 1
                continue
        if not email and not phone_norm:
            name_key = (first.lower(), last.lower())
            if name_key in seen_names:
                duplicates_name += 1
                continue

        # keep and update seen sets
        kept += 1
        if email:
            seen_emails.add(email)
        if phone_norm:
            seen_phones.add(phone_norm)
        if not email and not phone_norm:
            seen_names.add((first.lower(), last.lower()))

    total = len(transformed_rows)
    skipped = total - kept
    print(f"Total rows: {total}")
    print(f"Kept (expected imports): {kept}")
    print(f"Skipped total: {skipped}")
    print(f"  - Duplicates by email: {duplicates_email}")
    print(f"  - Duplicates by phone: {duplicates_phone}")
    print(f"  - Duplicates by name (no email/phone): {duplicates_name}")
    print(f"  - Missing both first & last: {missing_both_names}")
    print(f"  - Invalid phone (ignored, not skipped): {invalid_phone_count}")


if __name__ == "__main__":
    main()


