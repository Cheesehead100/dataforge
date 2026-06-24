"""Azure built-in role catalog: display name → role definition GUID."""

from __future__ import annotations

AZURE_ROLES: dict[str, str] = {
    # Storage
    "Storage Blob Data Reader": "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
    "Storage Blob Data Contributor": "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
    "Storage Blob Data Owner": "b7e6dc6d-f1e8-4753-8033-0f276bb0955b",
    "Storage Queue Data Reader": "19e7f393-937e-4f77-808e-94535e297925",
    "Storage Queue Data Contributor": "974c5e8b-45b9-4653-ba55-5f855dd0fb88",
    # Governance
    "Reader": "acdd72a7-3385-48ef-bd42-f606fba81ae7",
    "Contributor": "b24988ac-6180-42a0-ab88-20f7382dd24c",
    "Owner": "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",
    # Key Vault
    "Key Vault Secrets User": "4633458b-17de-408a-b874-0445c86b69e6",
    "Key Vault Secrets Officer": "b86a8fe4-44ce-4948-aee5-eccb2c155cd7",
    "Key Vault Reader": "21090545-7ca7-4776-b22c-e363652d74d4",
    # Data
    "Azure Event Hubs Data Receiver": "a638d3c7-ab3a-418d-83e6-5f17a39d4fde",
    "Azure Event Hubs Data Sender": "2b629674-e913-4c01-ae53-ef4638d8f975",
    "Azure Service Bus Data Receiver": "4f6d3b9f-027b-4f4c-9142-0e5a2a2247e0",
    "Azure Service Bus Data Sender": "69a216fc-b8fb-44d8-bc22-1f3c2cd27a39",
}


def role_definition_id(role_name: str) -> str:
    """Return the Azure role definition GUID for a given role display name."""
    try:
        return AZURE_ROLES[role_name]
    except KeyError as exc:
        known = ", ".join(sorted(AZURE_ROLES))
        raise ValueError(
            f"Unknown role '{role_name}'. Known roles: {known}"
        ) from exc
