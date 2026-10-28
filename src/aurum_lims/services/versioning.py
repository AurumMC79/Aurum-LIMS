"""Shared lifecycle for versioned master data (methods and specifications).

Draft ──activate (QA e-signature)──> Active ──(newer version activated, or retire)──> Retired

Only drafts can be edited. To change an active record, create a new version
(a draft copy), edit it, and activate it; the previous version is retired
automatically. Records in use keep pointing at the exact version they used.
"""

from __future__ import annotations

from enum import StrEnum


class VersionStatus(StrEnum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    RETIRED = "Retired"
