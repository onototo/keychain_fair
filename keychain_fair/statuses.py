UNPAID = "unpaid"
PAID = "paid"
STL_READY = "stl_ready"
QUEUED = "queued"
PRINTING = "printing"
PRINTED = "printed"
READY_FOR_PICKUP = "ready_for_pickup"
ERROR = "error"

ALLOWED_STATUSES = {
    UNPAID,
    PAID,
    STL_READY,
    QUEUED,
    PRINTING,
    PRINTED,
    READY_FOR_PICKUP,
    ERROR,
}

MANUAL_STATUSES = {
    UNPAID,
    PAID,
    PRINTED,
    READY_FOR_PICKUP,
    ERROR,
}

STATUS_LABELS = {
    UNPAID: "не оплачен",
    PAID: "оплачен",
    STL_READY: "STL готов",
    QUEUED: "в очереди",
    PRINTING: "печатается",
    PRINTED: "напечатан",
    READY_FOR_PICKUP: "готов к выдаче",
    ERROR: "ошибка",
}


def ensure_known_status(status: str) -> str:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Unknown order status: {status}")
    return status


def ensure_manual_status(status: str) -> str:
    if status not in MANUAL_STATUSES:
        raise ValueError(f"Status can not be set manually: {status}")
    return status
