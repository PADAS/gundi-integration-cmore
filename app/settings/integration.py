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
