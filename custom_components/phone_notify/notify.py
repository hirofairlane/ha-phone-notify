"""notify.phone_call — place a real phone call, speak a TTS message,
and listen for a spoken response, via the phone_notify MQTT bridge
(see addon/scripts/mqtt_bridge.py in the ha-phone-notify repo — this
integration is a thin wrapper around that bridge's MQTT protocol, it
does not do any of the calling/audio/recognition work itself).

configuration.yaml:

  notify:
    - platform: phone_notify
      name: phone_call
      topic_prefix: phone_notify        # must match the bridge's --mqtt-topic-prefix
      call_wait_timeout_seconds: 60     # optional, matches the bridge's own default
      listen_timeout_seconds: 40        # optional, matches the bridge's own default
      response_actions:                 # optional default, can be overridden per call
        cancel:
          keywords: ["cancelar", "cancel"]
        confirm:
          keywords: ["confirmo", "confirm"]

Usage:

  service: notify.phone_call
  data:
    message: "A water leak was detected in the kitchen"
    target:
      - "+34600000000"
    data:
      response_actions:
        cancel:
          keywords: ["cancelar"]
        confirm:
          keywords: ["confirmo"]

`target` is one or more literal phone numbers (E.164 recommended) —
this integration does not resolve `person.*`/`device_tracker.*`
entities to phone numbers, that mapping is left to your own
automation (e.g. build the target list with a template).

Multiple targets are called one at a time, in order — there's only
one phone, calls can't overlap. This does *not* stop early if an
earlier target confirms; each call fires its own `phone_notify_result`
event and it's up to your automation to decide whether to keep going
(e.g. by listening for the event and calling `notify.phone_call` again
conditionally, rather than passing every target up front).

Fires one `phone_notify_result` event per call, after each target is
attempted:

  event_type: phone_notify_result
  data:
    request_id: "<uuid>"
    phone_number: "+34600000000"
    message: "A water leak was detected in the kitchen"
    result: "cancel" | "confirm" | ... | "TIMEOUT" | "NO_CALL" | "ERROR" | "NO_RESPONSE"

`result` is whatever the bridge published (one of your configured
`response_actions` names, `TIMEOUT`, `NO_CALL`, or `ERROR` — see
mqtt_bridge.py's docstring), except `NO_RESPONSE`, which this
integration adds itself if the bridge never answers on MQTT at all
within the expected window (bridge not running, wrong topic_prefix,
broker unreachable, etc — check the bridge's own logs first).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.components.notify import (
    ATTR_DATA,
    ATTR_TARGET,
    PLATFORM_SCHEMA,
    BaseNotificationService,
)
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

CONF_TOPIC_PREFIX = "topic_prefix"
CONF_CALL_WAIT_TIMEOUT = "call_wait_timeout_seconds"
CONF_LISTEN_TIMEOUT = "listen_timeout_seconds"
CONF_RESPONSE_ACTIONS = "response_actions"

DEFAULT_TOPIC_PREFIX = "phone_notify"
DEFAULT_CALL_WAIT_TIMEOUT = 60
DEFAULT_LISTEN_TIMEOUT = 40

# Extra margin on top of the bridge's own two timeouts, so our own
# asyncio.wait_for doesn't fire a spurious NO_RESPONSE a moment before
# the bridge's real MQTT result would have arrived — dialing/adb/MQTT
# round-trip overhead isn't included in the bridge's own timers.
RESULT_WAIT_MARGIN_SECONDS = 15

EVENT_PHONE_NOTIFY_RESULT = "phone_notify_result"

RESPONSE_ACTIONS_SCHEMA = {
    cv.string: vol.Schema({vol.Required("keywords"): [cv.string]})
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_TOPIC_PREFIX, default=DEFAULT_TOPIC_PREFIX): cv.string,
        vol.Optional(CONF_CALL_WAIT_TIMEOUT, default=DEFAULT_CALL_WAIT_TIMEOUT): cv.positive_int,
        vol.Optional(CONF_LISTEN_TIMEOUT, default=DEFAULT_LISTEN_TIMEOUT): cv.positive_int,
        vol.Optional(CONF_RESPONSE_ACTIONS, default={}): RESPONSE_ACTIONS_SCHEMA,
    }
)


async def async_get_service(hass, config, discovery_info=None):
    """Set up the phone_call notify service."""
    return PhoneCallNotificationService(hass, config)


class PhoneCallNotificationService(BaseNotificationService):
    """Places a call via the phone_notify MQTT bridge and waits for its result."""

    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        self.hass = hass
        self._topic_prefix = config[CONF_TOPIC_PREFIX]
        self._default_call_wait_timeout = config[CONF_CALL_WAIT_TIMEOUT]
        self._default_listen_timeout = config[CONF_LISTEN_TIMEOUT]
        self._default_response_actions = config[CONF_RESPONSE_ACTIONS]

    async def async_send_message(self, message: str = "", **kwargs) -> None:
        targets = kwargs.get(ATTR_TARGET) or []
        if not targets:
            _LOGGER.error(
                "phone_notify: notify.phone_call called with no target phone "
                "number(s), nothing to call"
            )
            return

        data = kwargs.get(ATTR_DATA) or {}
        response_actions = data.get(CONF_RESPONSE_ACTIONS, self._default_response_actions)
        call_wait_timeout = data.get(CONF_CALL_WAIT_TIMEOUT, self._default_call_wait_timeout)
        listen_timeout = data.get(CONF_LISTEN_TIMEOUT, self._default_listen_timeout)

        for phone_number in targets:
            await self._call_one(
                phone_number, message, response_actions, call_wait_timeout, listen_timeout
            )

    async def _call_one(
        self, phone_number, message, response_actions, call_wait_timeout, listen_timeout
    ) -> None:
        request_id = uuid.uuid4().hex
        result_topic = f"{self._topic_prefix}/result/{request_id}"
        result_future: asyncio.Future = self.hass.loop.create_future()

        @callback
        def _on_result(msg) -> None:
            if result_future.done():
                return
            try:
                payload = json.loads(msg.payload)
                result_future.set_result(payload.get("result", "ERROR"))
            except (json.JSONDecodeError, TypeError):
                _LOGGER.warning("phone_notify: unparseable result payload: %r", msg.payload)
                result_future.set_result("ERROR")

        unsubscribe = await mqtt.async_subscribe(self.hass, result_topic, _on_result)

        try:
            await mqtt.async_publish(
                self.hass,
                f"{self._topic_prefix}/call",
                json.dumps(
                    {
                        "id": request_id,
                        "phone_number": phone_number,
                        "message": message,
                        "response_actions": response_actions,
                        "call_wait_timeout_seconds": call_wait_timeout,
                        "listen_timeout_seconds": listen_timeout,
                    }
                ),
            )
            _LOGGER.debug(
                "phone_notify: published call request %s for %s", request_id, phone_number
            )

            total_wait = call_wait_timeout + listen_timeout + RESULT_WAIT_MARGIN_SECONDS
            try:
                result = await asyncio.wait_for(result_future, timeout=total_wait)
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "phone_notify: no result published to %s within %ss — is the "
                    "phone_notify bridge running and connected to this broker with "
                    "topic_prefix=%r?",
                    result_topic, total_wait, self._topic_prefix,
                )
                result = "NO_RESPONSE"
        finally:
            unsubscribe()

        _LOGGER.debug("phone_notify: call to %s -> %s", phone_number, result)
        self.hass.bus.async_fire(
            EVENT_PHONE_NOTIFY_RESULT,
            {
                "request_id": request_id,
                "phone_number": phone_number,
                "message": message,
                "result": result,
            },
        )
