from enum import Enum


class Risk(str, Enum):
    READ = "read"
    LOW = "low"
    CONFIRM = "confirm"
    HIGH = "high"
    FORBIDDEN = "forbidden"


FORBIDDEN_TOPICS = {"price", "contract", "payment", "purchase_quantity", "negotiation", "order"}
CONFIRM_ACTIONS = {"send_external_message", "confirm_appointment"}
HIGH_RISK_ACTIONS = {
    "modify_confirmed_requirement",
    "move_confirmed_appointment",
    "cancel_confirmed_appointment",
    "resolve_calendar_conflict",
    "update_erp_contact",
    "update_erp_site",
}


def classify_action(action: str) -> Risk:
    if action in FORBIDDEN_TOPICS:
        return Risk.FORBIDDEN
    if action in HIGH_RISK_ACTIONS:
        return Risk.HIGH
    if action in CONFIRM_ACTIONS:
        return Risk.CONFIRM
    if action.startswith("read") or action in {
        "generate_plan",
        "create_message_draft",
        "create_tentative_hold",
    }:
        return Risk.LOW
    return Risk.CONFIRM
