# Add your integration-specific settings here
from environs import Env

env = Env()
env.read_env()

# GCS bucket where Gundi's sensors API stores event-attachment files. The
# routing layer hands destinations only the blob name (Attachment.file_path);
# this runner needs read access to the bucket to fetch the bytes. The env var
# name matches the classic dispatchers (cdip_connector BUCKET_NAME) so the
# same per-environment value can be reused.
ATTACHMENTS_BUCKET_NAME = env.str("BUCKET_NAME", None)

# Phase 0 of the reference-data design (docs/superpowers/specs/
# 2026-07-31-reference-data-config-ui-design.md): reference actions are only
# registered in Gundi once the platform accepts the "reference" action type.
# Until then this stays off so self-registration never sends a type the API
# would reject.
REGISTER_REFERENCE_ACTIONS = env.bool("REGISTER_REFERENCE_ACTIONS", False)
