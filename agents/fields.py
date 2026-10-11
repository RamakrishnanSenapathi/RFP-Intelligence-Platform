"""The 20 required output fields (19 extracted + generated Bid Summary), their groups and search seeds."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    name: str
    group: str
    description: str
    queries: tuple[str, ...]


DL, CL, PS = "dates_logistics", "commercial_legal", "product_specs"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("Bid Number", DL, "Official solicitation / RFP / IFB identifier (e.g. JA-207652).",
              ("bid number solicitation number RFP number", "invitation for bid reference number")),
    FieldSpec("Title", DL, "Full title of the bid or RFP.",
              ("title of bid RFP project name", "request for proposal for")),
    FieldSpec("Due Date", DL, "Final submission deadline incl. time and time zone, reflecting any addendum change. "
              "Format 'YYYY-MM-DD HH:MM TZ' (e.g. 2025-03-14 14:00 EST); if time or zone is not stated give what is stated.",
              ("bid due date submission deadline closing date and time", "proposals must be received no later than")),
    FieldSpec("Bid Submission Type", DL, "How the bid is submitted: electronic portal, sealed paper, email, etc. (with portal name/address if given).",
              ("how to submit bid electronically portal sealed envelope email", "submission instructions submit proposals to")),
    FieldSpec("Term of Bid", DL, "Contract duration and renewal options.",
              ("contract term duration renewal options", "initial term period of performance extension")),
    FieldSpec("Pre Bid Meeting", DL, "Date, time, location and whether attendance is mandatory; 'None' if documents say there is no pre-bid meeting.",
              ("pre-bid meeting pre-proposal conference mandatory attendance", "site visit questions deadline meeting")),
    FieldSpec("Installation", DL, "Whether installation / deployment / imaging services are required (Yes/No + details).",
              ("installation deployment imaging services required", "setup asset tagging configuration on-site")),
    FieldSpec("Delivery Date", DL, "Required delivery date or delivery window after award.",
              ("delivery date delivery within days after award", "delivery schedule lead time ARO")),
    FieldSpec("Bid Bond Requirement", CL, "Required bid bond / bid security with amount or percentage; 'Not required' if explicitly not required; null if bonds are never mentioned.",
              ("bid bond bid security surety percentage", "performance bond payment bond guarantee")),
    FieldSpec("Payment Terms", CL, "Payment terms (e.g. Net 30) and invoicing conditions.",
              ("payment terms net 30 invoice", "invoicing payment within days of receipt")),
    FieldSpec("Any Additional Documentation Required", CL, "Affidavits, forms, certificates, insurance, W-9 etc. to be submitted. List all, separated by '; '.",
              ("required forms affidavits certificates insurance W-9", "documents to submit with bid checklist attachments")),
    FieldSpec("MFG for Registration", CL, "Manufacturer(s) the bidder must be registered / authorized with.",
              ("manufacturer authorized reseller registration dealer authorization", "must be registered partner of manufacturer")),
    FieldSpec("Contract or Cooperative to use", CL, "Referenced cooperative / state contract vehicle, if any.",
              ("cooperative contract state contract piggyback", "contract vehicle master agreement NASPO")),
    FieldSpec("contact_info", CL, "Procurement contact: name, email, phone (format 'Name; email; phone').",
              ("procurement contact name email phone buyer", "questions should be directed to")),
    FieldSpec("company_name", CL, "Issuing organization / agency name.",
              ("issuing agency organization school district county department", "issued by")),
    FieldSpec("Model_no", PS, "Model number(s) of the requested product(s). Separate several with '; '.",
              ("model number product model", "make and model of equipment requested")),
    FieldSpec("Part_no", PS, "Part number(s) / SKU(s) of the requested product(s). Separate several with '; '.",
              ("part number SKU catalog number", "manufacturer part number")),
    FieldSpec("Product", PS, "Product name(s) and quantity (e.g. 'Dell Latitude laptops – qty 500').",
              ("product description quantity items to be purchased", "scope of work quantity of devices")),
    FieldSpec("Product Specification", PS, "Key technical specs: CPU, RAM, storage, display, OS, battery, ports, warranty, etc.",
              ("technical specifications processor memory storage display", "minimum specifications warranty requirements")),
)

FIELD_BY_NAME = {f.name: f for f in FIELDS}
GROUPS: dict[str, list[str]] = {}
for _f in FIELDS:
    GROUPS.setdefault(_f.group, []).append(_f.name)

SUMMARY_FIELD = "Bid Summary"
OUTPUT_ORDER = [
    "Bid Number", "Title", "Due Date", "Bid Submission Type", "Term of Bid", "Pre Bid Meeting", "Installation",
    "Bid Bond Requirement", "Delivery Date", "Payment Terms", "Any Additional Documentation Required",
    "MFG for Registration", "Contract or Cooperative to use", "Model_no", "Part_no", "Product", "contact_info",
    "company_name", SUMMARY_FIELD, "Product Specification",
]
assert set(OUTPUT_ORDER) == set(FIELD_BY_NAME) | {SUMMARY_FIELD}
