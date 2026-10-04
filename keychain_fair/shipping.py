from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ShipmentDraft:
    shipment_status: str
    shipment_id: str | None = None
    tracking_number: str | None = None


class ShipmentGateway(Protocol):
    def prepare(self, order: dict) -> ShipmentDraft:
        """Reserve a carrier shipment for an order.

        The manual gateway records that the sender will create the EuroPost
        application themselves. A future EuroPost gateway can call the carrier
        API here and return shipment_id without changing the order shape.
        """


class ManualShipmentGateway:
    def prepare(self, order: dict) -> ShipmentDraft:
        del order
        return ShipmentDraft(shipment_status="manual")
