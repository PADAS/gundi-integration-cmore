"""Tests for the reference-action contract and the four CMORE reference actions."""

import pytest


def test_reference_contract_types():
    from app.actions.core import (
        ActionConfiguration,
        ReferenceActionConfiguration,
        ReferenceDataResponse,
        ReferenceOption,
    )

    assert issubclass(ReferenceActionConfiguration, ActionConfiguration)

    response = ReferenceDataResponse(
        options=[ReferenceOption(value="Poacher Sighting", group="Wildlife")]
    )
    data = response.dict()
    assert data["options"][0]["value"] == "Poacher Sighting"
    assert data["options"][0]["label"] is None
    assert data["cache_ttl_seconds"] == 300
    assert data["truncated"] is False
