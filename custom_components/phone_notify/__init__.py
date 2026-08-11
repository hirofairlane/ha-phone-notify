"""Phone Call Notify — a notify.* platform, no top-level setup needed.

Everything lives in notify.py; this integration is configured entirely
under the `notify:` section of configuration.yaml, e.g.:

  notify:
    - platform: phone_notify
      name: phone_call
      topic_prefix: phone_notify
"""
