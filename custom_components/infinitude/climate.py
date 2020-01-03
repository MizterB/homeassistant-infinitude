"""
Platform for exposing a Carrier Infinity Touch climate device through the
Infinitude proxy application
"""
from homeassistant.components.climate import ClimateDevice, PLATFORM_SCHEMA
from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_HEAT_COOL, HVAC_MODE_FAN_ONLY,
    FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH,
    CURRENT_HVAC_OFF, CURRENT_HVAC_HEAT, CURRENT_HVAC_COOL, CURRENT_HVAC_IDLE,
    ATTR_TARGET_TEMP_HIGH, ATTR_TARGET_TEMP_LOW,
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_TARGET_TEMPERATURE_RANGE, SUPPORT_FAN_MODE, SUPPORT_PRESET_MODE)
from homeassistant.const import (
    CONF_HOST, CONF_PORT, ATTR_TEMPERATURE, TEMP_FAHRENHEIT, TEMP_CELSIUS, ATTR_ENTITY_ID)
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from urllib import request, parse
from urllib.error import URLError
import json
import datetime
import logging

_LOGGER = logging.getLogger(__name__)

# Hold states supported in the API
HOLD_ON = "on"
HOLD_OFF = "off"

# Hold types (assigned to thermostat display names)
HOLD_MODE_OFF = "per schedule"
HOLD_MODE_INDEFINITE = "hold"
HOLD_MODE_UNTIL = "hold until"

# Activity names supported in the API
ACTIVITY_HOME = "home"
ACTIVITY_AWAY = "away"
ACTIVITY_SLEEP = "sleep"
ACTIVITY_WAKE = "wake"
ACTIVITY_MANUAL = "manual"

# Activities are returned as a list by the API
# Lookup by index simplifies retrieval
ACTIVITY_HOME_INDEX = 0
ACTIVITY_AWAY_INDEX = 1
ACTIVITY_SLEEP_INDEX = 2
ACTIVITY_WAKE_INDEX = 3
ACTIVITY_MANUAL_INDEX = 4

# Preset modes supported by this component
PRESET_SCHEDULE = "Schedule"        # Restore the normal daily schedule
PRESET_HOME = "Home"                # Switch to 'Home' activity until the next schedule change
PRESET_AWAY = "Away"                # Switch to 'Away' activity until the next schedule change
PRESET_SLEEP = "Sleep"              # Switch to 'Sleep' activity until the next schedule change
PRESET_WAKE = "Wake"                # Switch to 'Wake' activity until the next schedule change
PRESET_MANUAL_TEMP = "Override"     # Override currently scheduled activity until the next schedule change
PRESET_MANUAL_PERM = "Hold"         # Override the schedule indefinitely

PRESET_MODES = [PRESET_SCHEDULE, PRESET_HOME, PRESET_AWAY, PRESET_SLEEP, PRESET_WAKE,
                PRESET_MANUAL_TEMP, PRESET_MANUAL_PERM]

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_PORT, default=3000): cv.port,
    vol.Optional('zone_names', default=[]): list,
})


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the connection"""
    host = config.get(CONF_HOST)
    port = config.get(CONF_PORT)

    infinitude = Infinitude(host, port)
    status = infinitude.status()

    devices = []

    # Create Infinitude devices for each zone that is enabled
    # Override the zone name if defined in the platform configuration
    zones = status["zones"][0]["zone"]
    for i in range(len(zones)):
        zone_name = None
        if "zone_names" in config and len(config["zone_names"]) >= i+1:
            zone_name = config["zone_names"][i]
        if zones[i]["enabled"][0] == "on":
            devices.append(InfinitudeZone(infinitude, zones[i]["id"], zone_name))
    add_devices(devices)

    def service_set_hold_mode(service):
        """Set the Hold Mode on the target thermostats."""
        # TODO: Add constants and a service schema?
        entity_id = service.data.get(ATTR_ENTITY_ID)
        mode = service.data.get("mode")
        until = service.data.get("until")
        activity = service.data.get("activity")

        if entity_id:
            target_zones = [device for device in devices
                            if device.entity_id in entity_id]
        else:
            target_zones = devices

        for zone in target_zones:
            zone.set_hold_mode(mode=mode, until=until, activity=activity)

    hass.services.register('infinitude', "set_hold_mode", service_set_hold_mode)
    return True


class Infinitude:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def api(self, path, params=None):
        url = "http://{}:{}{}".format(self.host, self.port, path)
        if params is not None:
            query = parse.urlencode(params)
            url = "{}?{}".format(url, query)
        _LOGGER.debug(url)
        req = request.Request(url)
        with request.urlopen(req) as response:
            data = json.loads(response.read().decode())
        _LOGGER.debug(data)
        return data

    def status(self):
        status = self.api("/api/status")
        return status

    def config(self):
        config = self.api("/api/config")
        return config["data"]


class InfinitudeZone(ClimateDevice):
    def __init__(self, infinitude, zone_id, zone_name_custom=None):
        self.infinitude = infinitude
        self.zone_id = zone_id
        self.zone_name_custom = zone_name_custom

        self.system_status = {}
        self.system_config = {}
        self.zone_status = {}
        self.zone_config = {}

        self._temperature_unit = None           # F, C
        self._current_temperature = None
        self._current_humidity = None
        self._hvac_mode = None                  # auto, heat, cool, off, fanonly
        self._hvac_action = None                # active_heat, active_cool, idle, more?
        self._fan_mode = None                   # off, high, med, low

        self.zone_name = None
        self.hold_state = None                  # on, off
        self.hold_activity = None               # home, away, sleep, wake, manual
        self.hold_until = None                  # HH:MM (on the quarter-hour)
        self.hold_mode = None                   # Computed - not in the API
        self.setpoint_heat = None
        self.setpoint_cool = None
        self.activity_current = None            # Computed - NOT the API status value
        self.activity_scheduled = None
        self.activity_scheduled_start = None
        self.activity_next = None
        self.activity_next_start = None
        self.occupancy = None                   # occupied, unoccupied, motion
        self.airflow_cfm = None
        self.outdoor_temperature = None

        self._preset_mode = None

        # Needed for API calls that update Zones, which use a zero-based zone index
        # Assuming that Zones are always listed in ascending order of their "ID" attribute
        # See https://github.com/nebulous/infinitude/issues/65#issuecomment-447971081
        self.zone_index = int(self.zone_id)-1

        # Populate with initial values
        self.update()

    @property
    def name(self):
        """Return the name of the climate device."""
        if self.zone_name_custom is not None:
            return self.zone_name_custom
        else:
            return self.zone_name

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    def update(self):

        def get_safe(source, key, index=0, empty_dict_as_none=True):
            """Helper function to safely parse JSON coming from Infinitude,
            where single values can be returned as lists"""
            result = None
            val = source.get(key, None)
            if val is None:
                result = None
            elif index is None:
                result = val
            else:
                result = val[index]
            if empty_dict_as_none and type(result) is dict and len(result) == 0:
                result = None
            return result

        # Retrieve full system status and config
        try:
            self.system_status = self.infinitude.status()
            self.system_config = self.infinitude.config()
        except URLError as e:
            _LOGGER.error("Unable to retrieve data from Infinitude: {}".format(e.reason))
            return

        # Parse system data for zone-specific information
        self.zone_status = next((z for z in get_safe(self.system_status, "zones")["zone"]
                                 if z["id"] == self.zone_id), None)
        self.zone_config = next((z for z in get_safe(self.system_config, "zones")["zone"]
                                 if z["id"] == self.zone_id), None)

        # These status values are always reliable
        self.zone_name = get_safe(self.zone_status, "name")
        self._temperature_unit = get_safe(self.system_config, "cfgem")
        self._current_temperature = float(get_safe(self.zone_status, "rt"))
        self._hvac_action = get_safe(self.zone_status, "zoneconditioning")
        self._current_humidity = float(get_safe(self.zone_status, "rh"))
        self._hvac_mode = get_safe(self.system_config, "mode")
        self.hold_state = get_safe(self.zone_config, "hold")
        self.hold_activity = get_safe(self.zone_config, "holdActivity")
        self.hold_until = get_safe(self.zone_config, "otmr")

        # Occupancy is not always present
        self.occupancy = get_safe(self.zone_status, "occupancy")

        # Only get CFM if IDU is present
        idu = get_safe(self.system_status, "idu")
        self.airflow_cfm = None
        if idu is not None:
            self.airflow_cfm = float(get_safe(idu, "cfm"))

        # Safely handle missing outdoor temperature
        oat = get_safe(self.system_status, "oat")
        if isinstance(oat, dict):
            self.outdoor_temperature = None
        else:
            self.outdoor_temperature = oat

        # These status values may be outdated if a pending
        # manual override was submitted via the API - see below
        self.setpoint_heat = float(get_safe(self.zone_status, "htsp"))
        self.setpoint_cool = float(get_safe(self.zone_status, "clsp"))
        self._fan_mode = get_safe(self.zone_status, "fan")
        self.activity_current = get_safe(self.zone_status, "currentActivity")

        # Status for setpoints and fan mode will only reflect API changes after an update/refresh cycle.
        # But we want the frontend to immediately reflect the new value, which is also stored
        # in the zone config.
        #
        # To get the true values, need to know what the current activity is.
        # If hold_activity=manual in the zone config, we know the current activity is manual,
        # even if the thermostat status does not yet reflect the change submitted via the API.
        # We can override with the correct values from the zone config.
        if get_safe(self.zone_config, "holdActivity") == "manual":
            activity_manual = next((a for a in get_safe(self.zone_config, "activities")["activity"]
                                   if a["id"] == "manual"), None)
            if activity_manual is not None:
                self.activity_current = "manual"
                self.setpoint_heat = float(get_safe(activity_manual, "htsp"))
                self.setpoint_cool = float(get_safe(activity_manual, "clsp"))
                self._fan_mode = get_safe(activity_manual, "fan")

        # Iterate through the system config to calculate the current and next schedule details
        # Looks for the next 'enabled' period in the zone program
        self.activity_scheduled = None
        self.activity_scheduled_start = None
        self.activity_next = None
        self.activity_next_start = None
        #'2019-12-15T16' does not match format '%Y-%m-%dT%H:%M:%S
        _LOGGER.debug(get_safe(self.system_status, "localTime"))
        try:
            dt = datetime.datetime.strptime(get_safe(self.system_status, "localTime")[:-6],
                                            "%Y-%m-%dT%H")  # Strip the TZ offset, since this is already in local time
        except ValueError:
            dt = datetime.datetime.strptime(get_safe(self.system_status, "localTime")[:-6],
                                            "%Y-%m-%dT%H:%M:%S")  # Strip the TZ offset, since this is already in local time
        while self.activity_next is None:
            day_name = dt.strftime("%A")
            program = next((day for day in get_safe(self.zone_config, "program")["day"] if day["id"] == day_name))
            for period in program["period"]:
                if get_safe(period, "enabled") == "off":
                    continue
                period_hh, period_mm = get_safe(period, "time").split(":")
                period_datetime = datetime.datetime(dt.year, dt.month, dt.day, int(period_hh), int(period_mm))
                if period_datetime < dt:
                    self.activity_scheduled = get_safe(period, "activity")
                    self.activity_scheduled_start = period_datetime
                if period_datetime >= dt:
                    self.activity_next = get_safe(period, "activity")
                    self.activity_next_start = period_datetime
                    break
            dt = datetime.datetime(year=dt.year, month=dt.month, day=dt.day) + datetime.timedelta(days=1)

        # Compute a custom 'hold_mode' based on the combination of hold values
        if self.hold_state == HOLD_ON:
            if self.hold_until is None:
                self.hold_mode = HOLD_MODE_INDEFINITE
            else:
                self.hold_mode = HOLD_MODE_UNTIL
        else:
            self.hold_mode = HOLD_MODE_OFF

        # Update the preset mode based on current state
        # If hold is off, preset is the currently scheduled activity
        if self.hold_mode == HOLD_MODE_OFF:
            if self.activity_scheduled == ACTIVITY_HOME:
                self._preset_mode = PRESET_HOME
            elif self.activity_scheduled == ACTIVITY_AWAY:
                self._preset_mode = PRESET_AWAY
            elif self.activity_scheduled == ACTIVITY_SLEEP:
                self._preset_mode = PRESET_SLEEP
            elif self.activity_scheduled == ACTIVITY_WAKE:
                self._preset_mode = PRESET_WAKE
            else:
                self._preset_mode = PRESET_SCHEDULE
        elif self.hold_mode == HOLD_MODE_UNTIL:
            # A temporary hold on the 'manual' activity is an 'override'
            if self.hold_activity == ACTIVITY_MANUAL:
                self._preset_mode = PRESET_MANUAL_TEMP
            # A temporary hold is on a non-'manual' activity is that activity
            else:
                if self.hold_activity == ACTIVITY_HOME:
                    self._preset_mode = PRESET_HOME
                elif self.hold_activity == ACTIVITY_AWAY:
                    self._preset_mode = PRESET_AWAY
                elif self.hold_activity == ACTIVITY_SLEEP:
                    self._preset_mode = PRESET_SLEEP
                elif self.hold_activity == ACTIVITY_WAKE:
                    self._preset_mode = PRESET_WAKE
        # An indefinite hold on any activity is a 'hold'
        else:
            self._preset_mode = PRESET_MANUAL_PERM

    @property
    def state(self):
        """Return the current state."""
        return super().state

    @property
    def precision(self):
        return super().precision

    @property
    def state_attributes(self):
        """Return the optional state attributes."""
        default_attributes = super().state_attributes
        custom_attributes = {
            "current_activity": self.activity_current,
            "scheduled_activity": self.activity_scheduled,
            "scheduled_activity_start": self.activity_scheduled_start,
            "next_activity": self.activity_next,
            "next_activity_start": self.activity_next_start,
            "hold_state": self.hold_state,
            "hold_activity": self.hold_activity,
            "hold_until": self.hold_until,
            "outdoor_temperature": self.outdoor_temperature,
            "airflow_cfm": self.airflow_cfm,
            "occupancy": self.occupancy
        }
        attributes = {}
        attributes.update(default_attributes)
        attributes.update(custom_attributes)
        return attributes

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        if self._temperature_unit == "C":
            return TEMP_CELSIUS
        else:
            return TEMP_FAHRENHEIT

    @property
    def current_humidity(self):
        """Return the current humidity."""
        return self._current_humidity

    @property
    def target_humidity(self):
        """Return the humidity we try to reach."""
        return super().target_humidity

    @property
    def hvac_mode(self):
        """Return hvac operation ie. heat, cool mode.
        Need to be one of HVAC_MODE_*.
        """
        if self._hvac_mode == 'heat':
            return HVAC_MODE_HEAT
        elif self._hvac_mode == 'cool':
            return HVAC_MODE_COOL
        elif self._hvac_mode == 'auto':
            return HVAC_MODE_HEAT_COOL
        elif self._hvac_mode == 'fanonly':
            return HVAC_MODE_FAN_ONLY
        elif self._hvac_mode == 'off':
            return HVAC_MODE_OFF
        else:
            return HVAC_MODE_OFF

    @property
    def hvac_modes(self):
        """Return the list of available hvac operation modes.
        Need to be a subset of HVAC_MODES.
        """
        return [HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_HEAT_COOL, HVAC_MODE_FAN_ONLY]

    @property
    def hvac_action(self):
        """Return the current running hvac operation if supported.
        Need to be one of CURRENT_HVAC_*.
        """
        # TODO: Add logic for fan
        if self.hvac_mode == HVAC_MODE_OFF:
            return CURRENT_HVAC_OFF
        elif self._hvac_action == 'idle':
            return CURRENT_HVAC_IDLE
        elif "heat" in self._hvac_action:
            return CURRENT_HVAC_HEAT
        elif "cool" in self._hvac_action:
            return CURRENT_HVAC_COOL
        else:
            return CURRENT_HVAC_IDLE

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""

        # Infinity 'auto' mode maps to HVAC_MODE_HEAT_COOL.
        # If enabled, set target temperature based on the current HVAC_action
        if self.hvac_mode == HVAC_MODE_HEAT_COOL:
            if self.hvac_action == CURRENT_HVAC_HEAT:
                return self.setpoint_heat
            elif self.hvac_action == CURRENT_HVAC_COOL:
                return self.setpoint_cool
            else:
                return self.current_temperature

        elif self.hvac_mode == HVAC_MODE_HEAT:
            return self.setpoint_heat

        elif self.hvac_mode == HVAC_MODE_COOL:
            return self.setpoint_cool

        else:
            return self.current_temperature

    @property
    def target_temperature_high(self):
        """Return the highbound target temperature we try to reach."""
        return self.setpoint_cool

    @property
    def target_temperature_low(self):
        """Return the lowbound target temperature we try to reach."""
        return self.setpoint_heat

    @property
    def preset_mode(self):
        """Return the current preset mode, e.g., home, away, temp.
        Requires SUPPORT_PRESET_MODE.
        """
        return self._preset_mode

    @property
    def preset_modes(self):
        """Return a list of available preset modes.
        Requires SUPPORT_PRESET_MODE.
        """
        return PRESET_MODES

    @property
    def is_aux_heat(self):
        """Return true if aux heater.
        Requires SUPPORT_AUX_HEAT.
        """
        raise NotImplementedError

    @property
    def fan_mode(self):
        """Return the fan setting.
        Requires SUPPORT_FAN_MODE.
        Infinity's internal value of 'off' displays as 'auto' on the thermostat
        """
        if self._fan_mode == "off":
            return FAN_AUTO
        elif self._fan_mode == "high":
            return FAN_HIGH
        elif self._fan_mode == "med":
            return FAN_MEDIUM
        elif self._fan_mode == "low":
            return FAN_LOW

    @property
    def fan_modes(self):
        """Return the list of available fan modes.
        Requires SUPPORT_FAN_MODE.
        """
        return [FAN_AUTO, FAN_HIGH, FAN_MEDIUM, FAN_LOW]

    @property
    def swing_mode(self):
        """Return the swing setting.
        Requires SUPPORT_SWING_MODE.
        """
        raise NotImplementedError

    @property
    def swing_modes(self):
        """Return the list of available swing modes.
        Requires SUPPORT_SWING_MODE.
        """
        raise NotImplementedError

    def set_temperature(self, **kwargs):
        """Set new target temperature."""
        data = {}
        if ATTR_TEMPERATURE in kwargs:
            if self.hvac_mode == HVAC_MODE_HEAT:
                self.setpoint_heat = kwargs["temperature"]
                data["htsp"] = kwargs["temperature"]
            elif self.hvac_mode == HVAC_MODE_COOL:
                self.setpoint_cool = kwargs["temperature"]
                data["clsp"] = kwargs["temperature"]

        if ATTR_TARGET_TEMP_HIGH in kwargs:
            self.setpoint_cool = kwargs[ATTR_TARGET_TEMP_HIGH]
            data["clsp"] = kwargs[ATTR_TARGET_TEMP_HIGH]

        if ATTR_TARGET_TEMP_LOW in kwargs:
            self.setpoint_heat = kwargs[ATTR_TARGET_TEMP_LOW]
            data["htsp"] = kwargs[ATTR_TARGET_TEMP_LOW]

        # Update the 'manual' activity with the updated temperatures
        # Enable hold until the next schedule change
        self.infinitude.api("/api/config/zones/zone/{}/activities/activity/{}/"
                            .format(self.zone_index, ACTIVITY_MANUAL_INDEX),
                            data)
        self.set_hold_mode(activity=ACTIVITY_MANUAL)

    def set_humidity(self, humidity):
        """Set new target humidity."""
        raise NotImplementedError

    def set_fan_mode(self, fan_mode):
        """Set new target fan mode.
        When set to 'auto', map to Infinity's internal value of 'off'
        """
        if fan_mode == FAN_AUTO:
            fan_mode = "off"

        # Update the 'manual' activity with the selected fan mode, preserving the current setbacks
        # Enable hold until the next schedule change
        self.infinitude.api("/api/config/zones/zone/{}/activities/activity/{}/"
                            .format(self.zone_index, ACTIVITY_MANUAL_INDEX),
                            {"fan": fan_mode, "htsp": self.setpoint_heat, "clsp": self.setpoint_cool})
        self.set_hold_mode(activity=ACTIVITY_MANUAL)

    def set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        if hvac_mode == HVAC_MODE_HEAT_COOL:
            data = {"mode": "auto"}
        elif hvac_mode == HVAC_MODE_HEAT:
            data = {"mode": "heat"}
        elif hvac_mode == HVAC_MODE_COOL:
            data = {"mode": "cool"}
        elif hvac_mode == HVAC_MODE_OFF:
            data = {"mode": "off"}
        elif hvac_mode == HVAC_MODE_FAN_ONLY:
            data = {"mode": "fanonly"}
        else:
            _LOGGER.error("Invalid HVAC mode: {}".format(hvac_mode))
            return
        self.infinitude.api("/api/config", data)

    def set_swing_mode(self, swing_mode):
        """Set new target swing operation."""
        raise NotImplementedError

    def set_preset_mode(self, preset_mode):
        """Set new preset mode."""
        # Skip if no change
        if preset_mode == self._preset_mode:
            return

        # For normal schedule, remove all holds
        if preset_mode == PRESET_SCHEDULE:
            self.set_hold_mode(mode=HOLD_MODE_OFF)

        # Activity override: Hold new activity until next schedule change
        elif preset_mode in [PRESET_HOME, PRESET_AWAY, PRESET_SLEEP, PRESET_WAKE]:
            if preset_mode == PRESET_HOME:
                activity = ACTIVITY_HOME
            elif preset_mode == PRESET_AWAY:
                activity = ACTIVITY_AWAY
            elif preset_mode == PRESET_SLEEP:
                activity = ACTIVITY_SLEEP
            elif preset_mode == PRESET_WAKE:
                activity = ACTIVITY_WAKE
            self.set_hold_mode(mode=HOLD_MODE_UNTIL, until=None, activity=activity)

        # Temporary manual override: Switch to manual activity and hold until next schedule change
        elif preset_mode == PRESET_MANUAL_TEMP:
            self.set_hold_mode(mode=HOLD_MODE_UNTIL, until=None, activity=ACTIVITY_MANUAL)

        # Permanent manual override: Switch to manual activity and hold indefinitely
        elif preset_mode == PRESET_MANUAL_PERM:
            self.set_hold_mode(mode=HOLD_MODE_INDEFINITE, until=None, activity=ACTIVITY_MANUAL)

        else:
            _LOGGER.error("Invalid preset mode: {}".format(preset_mode))
            return

    def turn_aux_heat_on(self):
        """Turn auxiliary heater on."""
        raise NotImplementedError

    def turn_aux_heat_off(self):
        """Turn auxiliary heater off."""
        raise NotImplementedError

    @property
    def supported_features(self):
        """Return the list of supported features."""
        baseline_features = (SUPPORT_FAN_MODE | SUPPORT_PRESET_MODE)
        if self.hvac_mode == HVAC_MODE_HEAT_COOL:
            return baseline_features | SUPPORT_TARGET_TEMPERATURE_RANGE
        elif self.hvac_mode in [HVAC_MODE_HEAT, HVAC_MODE_COOL]:
            return baseline_features | SUPPORT_TARGET_TEMPERATURE
        else:
            return baseline_features

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return super().min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return super().max_temp

    @property
    def min_humidity(self):
        """Return the minimum humidity."""
        return super().min_humidity

    @property
    def max_humidity(self):
        """Return the maximum humidity."""
        return super().max_humidity

    def set_hold_mode(self, **kwargs):
        """Update hold mode.
        Used to process various presets and support the legacy set_hold_mode service
        """
        # TODO: Validate inputs (mode name, time format, activity name)

        mode = kwargs.get("mode")
        # Default: Until time or next activity
        if mode is None:
            mode = HOLD_MODE_UNTIL

        until = kwargs.get("until")
        # Default: Next activity time
        if until is None:
            until = self.activity_next_start.strftime("%H:%M")

        activity = kwargs.get("activity")
        # Default: Current activity
        if activity is None:
            activity = self.activity_current

        if mode == HOLD_MODE_OFF:
            data = {"hold": HOLD_OFF, "holdActivity": "", "otmr": ""}
        elif mode == HOLD_MODE_INDEFINITE:
            data = {"hold": HOLD_ON, "holdActivity": activity, "otmr": ""}
        elif mode == HOLD_MODE_UNTIL:
            data = {"hold": HOLD_ON, "holdActivity": activity, "otmr": until}
        else:
            _LOGGER.error("Invalid hold mode: {}".format(mode))
            return

        self.infinitude.api("/api/config/zones/zone/{}/".format(self.zone_index), data)
