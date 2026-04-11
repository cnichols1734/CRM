"""
Tax Protest blueprint.
Lets agents search their CRM contacts, locate properties in county tax data,
extract subdivisions via LLM, find lower-value comparables, and download CSV.
"""

import csv
import re
from io import StringIO

from flask import Blueprint, render_template, request, jsonify, Response, abort, session
from flask_login import login_required, current_user

from models import db, Contact
from feature_flags import feature_required
from services.tenant_service import org_query, can_view_all_org_data
from services.tax_protest_service import (
    find_property_in_tax_data,
    extract_subdivision_llm,
    find_comparables,
    get_neighborhood_name,
    get_subdivision_stats,
    cache_search_result,
    get_cached_search_result,
    get_main_property_by_id,
    _is_valid_subdivision,
)

tax_protest_bp = Blueprint("tax_protest", __name__, url_prefix="/tax-protest")


def _authorized_contact(contact_id):
    """Load a contact with org + ownership checks. Returns contact or aborts 403."""
    contact = org_query(Contact).filter_by(id=contact_id).first()
    if not contact:
        abort(404, description="Contact not found")
    if not can_view_all_org_data() and contact.user_id != current_user.id:
        abort(403, description="You can only search your own contacts")
    return contact


@tax_protest_bp.route("/")
@login_required
@feature_required("TAX_PROTEST")
def index():
    """Main Tax Protest page."""
    return render_template("tax_protest/index.html")


@tax_protest_bp.route("/search-contacts")
@login_required
@feature_required("TAX_PROTEST")
def search_contacts():
    """AJAX endpoint: search CRM contacts by name or address."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    query = org_query(Contact)

    if not can_view_all_org_data():
        query = query.filter(Contact.user_id == current_user.id)

    search_term = f"%{q}%"
    query = query.filter(
        db.or_(
            Contact.first_name.ilike(search_term),
            Contact.last_name.ilike(search_term),
            db.func.concat(Contact.first_name, " ", Contact.last_name).ilike(
                search_term
            ),
            Contact.street_address.ilike(search_term),
            Contact.city.ilike(search_term),
            Contact.zip_code.ilike(search_term),
        )
    ).limit(15)

    results = []
    for c in query.all():
        addr_parts = [p for p in [c.street_address, c.city, c.state, c.zip_code] if p]
        results.append(
            {
                "id": c.id,
                "name": f"{c.first_name} {c.last_name}",
                "address": ", ".join(addr_parts),
                "street_address": c.street_address,
                "city": c.city,
                "state": c.state,
                "zip_code": c.zip_code,
            }
        )

    return jsonify(results)


@tax_protest_bp.route("/search", methods=["POST"])
@login_required
@feature_required("TAX_PROTEST")
def search_property():
    """Search tax data for a contact's property and find comparables."""
    data = request.get_json()
    if not data or not data.get("contact_id"):
        return jsonify({"error": "contact_id required"}), 400

    contact = _authorized_contact(data["contact_id"])

    if not contact.street_address:
        return jsonify({"error": "Contact has no street address on file"}), 400

    property_record, source = find_property_in_tax_data(
        contact.street_address, contact.city, contact.zip_code
    )

    if not property_record:
        return jsonify(
            {
                "error": f'No property found matching "{contact.street_address}" in Chambers, Harris, Liberty, or Fort Bend County tax records'
            }
        ), 404

    market_value = property_record.get("market_value")
    zip_code = property_record.get("zip")
    neighborhood_code = property_record.get("neighborhood_code")
    subdivision_code = property_record.get("subdivision_code")
    main_sq_ft = property_record.get("sq_ft")
    main_acreage = property_record.get("acreage")
    subdivision = None
    fuzzy = False

    if source == "hcad":
        lgl_2 = property_record.get("legal2") or ""
        if _is_valid_subdivision(lgl_2):
            subdivision = lgl_2.strip()
        else:
            subdivision = extract_subdivision_llm(property_record.get("legal1", ""))
            fuzzy = True
        if not subdivision:
            return jsonify(
                {
                    "error": "Could not determine subdivision from property legal description",
                    "main_property": property_record,
                    "source": source,
                }
            ), 422
        comparables = find_comparables(
            subdivision,
            zip_code,
            market_value,
            source,
            main_sq_ft=main_sq_ft,
            fuzzy_subdivision=fuzzy,
            main_acreage=main_acreage,
        )
    elif source == "liberty":
        subdivision = property_record.get("subdivision")
        if not subdivision or not subdivision_code:
            return jsonify(
                {
                    "error": "Could not determine Liberty subdivision from tax data",
                    "main_property": property_record,
                    "source": source,
                }
            ), 422
        comparables = find_comparables(
            subdivision,
            zip_code,
            market_value,
            source,
            main_sq_ft=main_sq_ft,
            subdivision_code=subdivision_code,
            main_acreage=main_acreage,
        )
    elif source == "fort_bend":
        subdivision = property_record.get("subdivision")
        if not subdivision or not subdivision_code:
            return jsonify(
                {
                    "error": "Could not determine Fort Bend neighborhood from tax data",
                    "main_property": property_record,
                    "source": source,
                }
            ), 422
        comparables = find_comparables(
            subdivision,
            zip_code,
            market_value,
            source,
            main_sq_ft=main_sq_ft,
            subdivision_code=subdivision_code,
            main_acreage=main_acreage,
        )
    else:
        legal_desc = property_record.get("legal1", "")
        subdivision = extract_subdivision_llm(legal_desc)
        if not subdivision:
            return jsonify(
                {
                    "error": "Could not extract subdivision from property legal description",
                    "main_property": property_record,
                    "source": source,
                }
            ), 422
        comparables = find_comparables(
            subdivision,
            zip_code,
            market_value,
            source,
            main_sq_ft=main_sq_ft,
            main_acreage=main_acreage,
        )

    cache_search_result(
        source=source,
        subdivision=subdivision,
        main_property_id=property_record["id"],
        contact_id=contact.id,
        zip_code=zip_code,
        neighborhood_code=neighborhood_code,
        subdivision_code=subdivision_code,
        main_sq_ft=main_sq_ft,
        main_acreage=main_acreage,
        fuzzy_subdivision=fuzzy,
    )

    subdivision_stats = get_subdivision_stats(
        subdivision,
        zip_code,
        market_value,
        source,
        fuzzy_subdivision=fuzzy,
        subdivision_code=subdivision_code,
    )

    return jsonify(
        {
            "source": source,
            "subdivision": subdivision,
            "main_property": property_record,
            "comparables": comparables,
            "total_comparables": len(comparables),
            "subdivision_stats": subdivision_stats,
        }
    )


@tax_protest_bp.route("/download-csv")
@login_required
@feature_required("TAX_PROTEST")
def download_csv():
    """Download CSV of comparables using cached search result (no LLM re-run)."""
    cached = get_cached_search_result()
    if not cached:
        abort(400, description="No search results to download. Run a search first.")

    contact = _authorized_contact(cached["contact_id"])

    main_property = get_main_property_by_id(
        cached["main_property_id"], cached["source"]
    )
    if not main_property:
        abort(404, description="Main property no longer found in tax data")

    comparables = find_comparables(
        cached["subdivision"],
        cached["zip_code"],
        main_property["market_value"],
        cached["source"],
        main_sq_ft=cached.get("main_sq_ft"),
        fuzzy_subdivision=cached.get("fuzzy_subdivision", False),
        subdivision_code=cached.get("subdivision_code"),
        main_acreage=cached.get("main_acreage"),
    )

    output = StringIO()
    writer = csv.writer(output)

    headers = [
        "Type",
        "Address",
        "City",
        "Zip",
        "Market Value",
        "Sq Ft",
        "Acreage",
        "Subdivision",
        "Legal Description",
        "Account",
        "County",
    ]
    writer.writerow(headers)

    county = {
        "chambers": "Chambers",
        "hcad": "Harris",
        "liberty": "Liberty",
        "fort_bend": "Fort Bend",
    }.get(cached["source"], cached["source"].title())
    subdivision = cached.get("subdivision", "")

    def write_row(prop, row_type="Comparable"):
        writer.writerow(
            [
                row_type,
                prop.get("full_address", ""),
                prop.get("city", ""),
                prop.get("zip", ""),
                prop.get("market_value", ""),
                prop.get("sq_ft", ""),
                prop.get("acreage", ""),
                subdivision,
                prop.get("legal1", ""),
                prop.get("account", ""),
                county,
            ]
        )

    write_row(main_property, "Subject Property")
    for comp in comparables:
        write_row(comp)

    output.seek(0)

    addr = contact.street_address or "unknown"
    filename = re.sub(r"[^a-zA-Z0-9]+", "_", addr).strip("_") + ".csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": "text/csv",
        },
    )
