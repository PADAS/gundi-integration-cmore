from datetime import datetime
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class TrackType(str, Enum):
    OWN_TRACK = "OwnTrack"
    SYSTEM_TRACK = "SystemTrack"
    SENSOR_TRACK = "SensorTrack"
    MANUAL_TRACK = "ManualTrack"
    EMITTER_POSITION = "EmitterPosition"


class Affiliation(str, Enum):
    UNKNOWN = "Unknown"
    FRIENDLY = "Friendly"
    HOSTILE = "Hostile"
    NEUTRAL = "Neutral"


class CmoreClassification(BaseModel):
    """MIL2525b-based client classification — drives the icon rendered on the C-more map.

    Valid values are instance-specific (GET /v2/clients/get_classification_tree);
    plain strings are used here instead of enums so custom trees keep working.
    Unspecified fields default to UNKNOWN server-side.
    """
    battleDimension: Optional[str] = None
    force: Optional[str] = None
    type: Optional[str] = None
    role: Optional[str] = None


class CmoreLocation(BaseModel):
    clientId: int
    latitude: float
    longitude: float
    timestamp: datetime
    altitude: Optional[float] = None
    accuracy: Optional[float] = None
    heading: Optional[float] = None
    speed: Optional[float] = None
    source: Optional[str] = None


class CmoreProperty(BaseModel):
    clientId: int
    name: str
    value: str


class CmoreTagValue(BaseModel):
    fieldId: int
    value: str


class CmoreEventTag(BaseModel):
    tagId: int
    values: List[CmoreTagValue] = Field(default_factory=list)


class UploadType(str, Enum):
    GENERATED = "Generated"
    MOBILE = "Mobile"
    WEBSITE = "Website"
    UNKNOWN = "Unknown"


class CmoreEvent(BaseModel):
    description: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    accuracy: Optional[float] = None
    dateOccurred: Optional[datetime] = None
    uploadType: UploadType = UploadType.GENERATED
    ownerGroupId: Optional[int] = None
    tags: Optional[List[CmoreEventTag]] = None


class CmoreComment(BaseModel):
    """Request body for POST /comment.

    CMORE represents follow-up annotations on an event as comments. This
    integration uses them to forward EarthRanger event edits (new notes,
    field changes) to the corresponding CMORE event.
    """
    description: str
    rootMessageId: int
    uploadType: UploadType = UploadType.GENERATED


class CmoreVirtualClientRequest(BaseModel):
    trackSource: str
    trackNo: int
    trackType: TrackType = TrackType.OWN_TRACK
    targetId: Optional[str] = None
    callsign: Optional[str] = None
    affiliation: Affiliation = Affiliation.UNKNOWN
    trackSourceType: Optional[str] = None
    classification: Optional[CmoreClassification] = None


class CmoreGNode(BaseModel):
    """Response object returned by POST /v2/clients/virtual."""
    clientId: int
    trackNo: int
    trackSource: str
    trackType: str
    error: Optional[str] = None
    data: Optional[Any] = None


class CmoreGatewayMapping(BaseModel):
    """Returned by GET /v2/clients/virtual/gateway_mapping."""
    clientId: int
    trackNo: int
    trackSource: str
