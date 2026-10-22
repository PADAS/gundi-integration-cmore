# gundi-integration-cmore
Gundi Action Runner for CMORE.

## Activity-log redaction

The configuration attached to every activity-log event is redacted before publishing (`app/services/redaction.py`): values under secret-looking keys (`password`, `token`, `api_key`, `secret`, ...) and fields your config model declares as `SecretStr`, `Field(format="password")`, `UIOptions(widget="password")` or `Field(writeOnly=True)` are replaced with `**********`, matched by field name or alias and at any depth of nested models. Declare secrets that way and they never reach the portal's activity log in clear, whatever their name. `writeOnly=True` is the declaration that leaves the portal form unchanged: use it for a secret that is not a password input, such as a JSON document of device keys. The failed request and response attached to a failure event (`request_url`, `request_data`, `server_response_body`) are masked by the same key rules, so a rejected password-grant token POST does not carry the password into the log either; secret-looking query parameters quoted in the error text and traceback (an httpx `raise_for_status()` message names the full URL) are masked too.
