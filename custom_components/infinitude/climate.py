"""
Platform for exposing a Carrier Infinity Touch climate device through the
Infinitude proxy application
"""
from homeassistant.components.climate import ClimateDevice, PLATFORM_SCHEMA
from homeassistant.components.climate.const import (
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_TARGET_TEMPERATURE_HIGH, SUPPORT_TARGET_TEMPERATURE_LOW,
    SUPPORT_AWAY_MODE, SUPPORT_HOLD_MODE, SUPPORT_FAN_MODE,
    SUPPORT_OPERATION_MODE,
    STATE_AUTO, STATE_COOL, STATE_HEAT, STATE_IDLE, STATE_FAN_ONLY,
    ATTR_TARGET_TEMP_LOW, ATTR_TARGET_TEMP_HIGH)

from homeassistant.const import (
    CONF_HOST, CONF_PORT, STATE_ON, STATE_OFF, ATTR_TEMPERATURE, TEMP_FAHRENHEIT, TEMP_CELSIUS)

import voluptuous as vol
import homeassistant.helpers.config_validation as cv

import sys
import urllib.request, urllib.parse
import json
import datetime
import logging

_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = []

SUPPORT_FLAGS = (SUPPORT_TARGET_TEMPERATURE | SUPPORT_TARGET_TEMPERATURE_HIGH | SUPPORT_TARGET_TEMPERATURE_LOW |
				 SUPPORT_AWAY_MODE | SUPPORT_HOLD_MODE | SUPPORT_FAN_MODE |
				 SUPPORT_OPERATION_MODE)

HOLD_MODE_PERSCHEDULE = "per schedule"
HOLD_MODE_HOLD = "hold"
HOLD_MODE_HOLDUNTIL = "hold until"

FAN_MODE_AUTO = "auto" # Display value is "auto", but internal config value is "off"
FAN_MODE_HIGH = "high"
FAN_MODE_MED = "med"
FAN_MODE_LOW = "low"

ACTIVITY_INDEX = {
    "home": 0,
    "away": 1,
    "sleep": 2,
    "away": 3,
    "manual": 4
}

OPERATION_LIST = [STATE_AUTO, STATE_HEAT, STATE_COOL, STATE_OFF, STATE_FAN_ONLY]
FAN_LIST = [FAN_MODE_AUTO, FAN_MODE_HIGH, FAN_MODE_MED, FAN_MODE_LOW]

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_PORT, default=3000): cv.port,
})

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the connection"""
    host = config.get(CONF_HOST)
    port = config.get(CONF_PORT)

    infinitude = Infinitude(host, port)
    status = infinitude.getStatus()

    devices = []
    zones = status["zones"][0]["zone"]
    for i in range(len(zones)):
        zoneName = None
        if "zone_names" in config and len(config["zone_names"]) >= i+1:
            zoneName = config["zone_names"][i]
        if zones[i]["enabled"][0] == "on":
            devices.append(InfinitudeZone(infinitude, zones[i]["id"], zoneName))
    add_devices(devices)

class Infinitude():
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def callAPI(self, path, params=None):
        url = "http://{}:{}{}".format(self.host, self.port, path)
        if params is not None:
            queryString = urllib.parse.urlencode(params)
            url = "{}?{}".format(url, queryString)
        _LOGGER.debug(url)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
        _LOGGER.debug(data)
        return data

    def getStatus(self):
        status = self.callAPI("/api/status")
        return status

    def getConfig(self):
        config = self.callAPI("/api/config")
        return config["data"]

class InfinitudeZone(ClimateDevice):
    def __init__(self, infinitude, zoneID, customName=None):
        self._infinitude = infinitude
        self._zoneID = zoneID
        self._customName = customName
        self._systemStatus = {}
        self._systemConfig = {}
        self._zoneStatus = {}
        self._zoneConfig = {}

        # Needed for API calls that update Zones, which use a zero-based zone index
        # Assuming that Zones are always listed in ascending order of their "ID" attribute
        # See https://github.com/nebulous/infinitude/issues/65#issuecomment-447971081
        self._zoneIndex = int(zoneID)-1

        self.update()

    def update(self):
        def getSafe(source, key, index=0):
            val = source.get(key, None)
            if val is None:
                return None
            elif index is None:
                return val
            else:
                return val[index]

        try:
            self._systemStatus = self._infinitude.getStatus()
            self._systemConfig = self._infinitude.getConfig()
        except:
            _LOGGER.error("Unable to retrieve data from Infinitude.  Error message: {}".format(sys.exc_info()[0]))
            return
        self._zoneStatus = next((z for z in getSafe(self._systemStatus, "zones")["zone"] if z["id"] == self._zoneID), None)
        self._zoneConfig = next((z for z in getSafe(self._systemConfig, "zones")["zone"] if z["id"] == self._zoneID), None)

        # These status values are always reliable
        self._name = getSafe(self._zoneStatus, "name")
        self._temperature = float(getSafe(self._zoneStatus, "rt"))
        self._hvacState = getSafe(self._zoneStatus, "zoneconditioning")         # active_heat, active_cool, idle, more?
        self._relativeHumidity = float(getSafe(self._zoneStatus, "rh"))
        self._operatingMode = getSafe(self._systemConfig, "mode")               # auto, heat, cool, off, fanonly
        self._mode = getSafe(self._systemStatus, "mode")                        # hpheat, gasheat, off, cool?, auto?, fan?
        self._holdState = getSafe(self._zoneConfig, "hold")                     # on, off
        self._holdActivity = getSafe(self._zoneConfig, "holdActivity")          # home, away, sleep, wake, manual
        self._holdUntil = getSafe(self._zoneConfig, "otmr")                     # HH:MM (on the quarter-hour)
        self._occupancy = getSafe(self._zoneStatus, "occupancy")                # occupied, unoccupied, motion
        self._units = getSafe(self._systemConfig, "cfgem")                      # F, C

        # Only get CFM if IDU is present
        idu = getSafe(self._systemStatus, "idu")
        self._cfm = None
        if idu is not None:
            self._cfm = float(getSafe(idu, "cfm"))

        # Safely handle missing outdoor temperature
        oat = getSafe(self._systemStatus, "oat")
        if isinstance(oat, dict):
            self._outdoorTemperature = None
        else:
            self._outdoorTemperature = oat

        # These status values may be outdated if a pending
        # manual override was submitted via the API - see below
        self._setbackHeat = float(getSafe(self._zoneStatus, "htsp"))
        self._setbackCool = float(getSafe(self._zoneStatus, "clsp"))
        self._fanMode = getSafe(self._zoneStatus, "fan")                        # off, high, med, low
        self._currentActivity = getSafe(self._zoneStatus, "currentActivity")    # home, away, sleep, wake, manual

        # Status for setbacks and fan mode will only reflect API changes after an update/refresh cycle.
        # But we want the frontend to immediately reflect the new value, which is also stored
        # in the zone config.
        #
        # To get the true values, need to know what the current activity is.
        # If holdActivity=manual in the zone config, we know the current activity is manual,
        # even if the thermostat status does not yet reflect the change submitted via the API.
        # We can override with the correct values from the zone config.
        if getSafe(self._zoneConfig, "holdActivity") == "manual":
            manualActivity = next((a for a in getSafe(self._zoneConfig, "activities")["activity"] if a["id"] == "manual"), None)
            if manualActivity is not None:
                self._currentActivity = "manual"
                self._setbackHeat = float(getSafe(manualActivity, "htsp"))
                self._setbackCool = float(getSafe(manualActivity, "clsp"))
                self._fanMode = getSafe(manualActivity, "fan")

        # Iterate through the system config to calculate the current and next schedule details
        # Looks for the next 'enabled' period in the zone program
        self._scheduledActivity = None
        self._scheduledActivityStart = None
        self._nextActivity = None
        self._nextActivityStart = None
        dt = datetime.datetime.strptime(getSafe(self._systemStatus, "localTime")[:-6],
                                        "%Y-%m-%dT%H:%M:%S")  # Strip the TZ offset, since this is already in local time
        while self._nextActivity is None:
            dayName = dt.strftime("%A")
            program = next((day for day in getSafe(self._zoneConfig, "program")["day"] if day["id"] == dayName))
            for period in program["period"]:
                if getSafe(period, "enabled") == "off":
                    continue
                periodHH, periodMM = getSafe(period, "time").split(":")
                periodDt = datetime.datetime(dt.year, dt.month, dt.day, int(periodHH), int(periodMM))
                if periodDt < dt:
                    self._scheduledActivity = getSafe(period, "activity")
                    self._scheduledActivityStart = periodDt
                if periodDt >= dt:
                    self._nextActivity = getSafe(period, "activity")
                    self._nextActivityStart = periodDt
                    break
            dt = datetime.datetime(year=dt.year, month=dt.month, day=dt.day) + datetime.timedelta(days=1)

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_FLAGS

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        if self._units == "C":
            return TEMP_CELSIUS
        else:
            return TEMP_FAHRENHEIT

    @property
    def should_poll(self):
        """Return the polling state."""
        return True
    @property
    def device_state_attributes(self):
        """Return the device specific state attributes."""
        attributes = {
            "hvac_state": self._hvacState,
            "hvac_mode": self._mode,
            "outdoor_temperature": self._outdoorTemperature,
            "airflow_cfm": self._cfm,
            "occupancy": self._occupancy,
            "current_activity": self._currentActivity,
            "hold_state": self._holdState,
            "hold_activity": self._holdActivity,
            "hold_until": self._holdUntil,
            "scheduled_activity": self._scheduledActivity,
            "scheduled_activity_start": self._scheduledActivityStart,
            "next_activity": self._nextActivity,
            "next_activity_start": self._nextActivityStart,
        }
        return attributes

    @property
    def name(self):
        """Return the name of the climate device."""
        if self._customName is not None:
            return self._customName
        else:
            return self._name

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if self.current_operation == STATE_AUTO:
            if self.state == STATE_HEAT:
                return self._setbackHeat
            elif self.state == STATE_COOL:
                return self._setbackCool
            else:
                return None
        elif self.current_operation == STATE_HEAT:
            return self._setbackHeat
        elif self.current_operation == STATE_COOL:
            return self._setbackCool
        else:
            return None

    @property
    def target_temperature_high(self):
        """Return the highbound target temperature we try to reach."""
        if self.current_operation == STATE_AUTO:
            return self._setbackCool
        return None

    @property
    def target_temperature_low(self):
        """Return the lowbound target temperature we try to reach."""
        if self.current_operation == STATE_AUTO:
            return self._setbackHeat
        return None

    @property
    def current_humidity(self):
        """Return the current humidity."""
        return self._relativeHumidity

    @property
    def state(self):
        """Return the current state."""
        if "heat" in self._hvacState:
            return STATE_HEAT
        elif "cool" in self._hvacState:
            return STATE_COOL
        if self.current_operation == STATE_OFF:
            return STATE_OFF
        return STATE_IDLE

    @property
    def current_operation(self):
        """Return current operation mode"""
        if self._operatingMode == "auto":
            return STATE_AUTO
        elif self._operatingMode == "heat":
            return STATE_HEAT
        elif self._operatingMode == "cool":
            return STATE_COOL
        elif self._operatingMode == "off":
            return STATE_OFF
        elif self._operatingMode == "fanonly":
            return STATE_FAN_ONLY
        else:
            return None

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        return OPERATION_LIST

    @property
    def is_away_mode_on(self):
        """Return if away mode is on."""
        # True if running an indefinite hold on the 'away' activity -- NOT if this just running the 'away' schedule
        return (self._holdActivity == "away" and self._holdState == "on" and self._holdUntil == {})

    @property
    def current_hold_mode(self):
        """Return hold mode setting."""
        if self._holdState == "off":
            return HOLD_MODE_PERSCHEDULE
        elif self._holdUntil is not None:
            return HOLD_MODE_HOLDUNTIL
        return HOLD_MODE_HOLD

    @property
    def is_on(self):
        """Return true if the device is on."""
        return self.current_operation != STATE_OFF

    @property
    def current_fan_mode(self):
        """Return the fan setting."""
        # This is the display value, so translate "off" to "auto"
        if self._fanMode == "off":
            return FAN_MODE_AUTO
        else:
            return self._fanMode

    @property
    def fan_list(self):
        """Return the list of available fan modes."""
        # These are display values, so use "auto" instead of "off"
        return FAN_LIST

    def set_temperature(self, **kwargs):
        data = {}
        # Get the updated temperature values
        if ATTR_TEMPERATURE in kwargs:
            if self.current_operation == STATE_HEAT:
                self._setbackHeat = kwargs["temperature"]
                data["htsp"] = kwargs["temperature"]
            elif self.current_operation == STATE_COOL:
                self._setbackCool = kwargs["temperature"]
                data["clsp"] = kwargs["temperature"]
        if ATTR_TARGET_TEMP_HIGH in kwargs:
            self._setbackCool = kwargs[ATTR_TARGET_TEMP_HIGH]
            data["clsp"] = kwargs[ATTR_TARGET_TEMP_HIGH]
        if ATTR_TARGET_TEMP_LOW in kwargs:
            self._setbackHeat = kwargs[ATTR_TARGET_TEMP_LOW]
            data["htsp"] = kwargs[ATTR_TARGET_TEMP_LOW]

        # Update the 'manual' activity with the updated temperatures
        # Switch to the 'manual' activity and hold until the next scheduled activity
        self._infinitude.callAPI("/api/config/zones/zone/{}/activities/activity/{}/".format(self._zoneIndex, ACTIVITY_INDEX["manual"]), data)
        self.set_hold_mode(HOLD_MODE_HOLDUNTIL, activity="manual")

    def set_fan_mode(self, fan):
        """Set new fan mode."""
        # Translate 'auto' display value to config value
        if fan == FAN_MODE_AUTO:
            fan = "off"

        # Update the 'manual' activity with the selected fan mode, preserving the current setbacks
        # Switch to the 'manual' activity and hold until the next scheduled activity
        self._infinitude.callAPI("/api/config/zones/zone/{}/activities/activity/{}/".format(self._zoneIndex, ACTIVITY_INDEX["manual"]), {"fan" : fan, "htsp" : self._setbackHeat, "clsp" : self._setbackCool})
        self.set_hold_mode(HOLD_MODE_HOLDUNTIL, activity="manual")

    def set_operation_mode(self, operation_mode):
        """Set new operation mode."""
        if operation_mode == STATE_AUTO:
            data = {"mode" : "auto"}
        elif operation_mode == STATE_HEAT:
            data = {"mode" : "heat"}
        elif operation_mode == STATE_COOL:
            data = {"mode" : "cool"}
        elif operation_mode == STATE_OFF:
            data = {"mode" : "off"}
        elif operation_mode == STATE_FAN_ONLY:
            data = {"mode" : "fanonly"}
        self._infinitude.callAPI("/api/config", data)

    def turn_away_mode_on(self):
        """Turn away mode on."""
        # Sets an indefinite hold on the 'away' activity
        self.set_hold_mode(HOLD_MODE_HOLD, activity="away")

    def turn_away_mode_off(self):
        """Turn away mode off."""
        # Clears all hold settings
        self.set_hold_mode(HOLD_MODE_PERSCHEDULE)

    def set_hold_mode(self, hold, **kwargs):
        """Update hold mode."""
        activity = kwargs.get("activity", self._currentActivity)
        until = kwargs.get("until", self._nextActivityStart.strftime("%H:%M"))  # Default hold until next scheduled period
        if hold == HOLD_MODE_PERSCHEDULE:
            data = {"hold": "off", "holdActivity": "", "otmr": ""}
        elif hold == HOLD_MODE_HOLD:
            data = {"hold": "on", "holdActivity": activity, "otmr": ""}
        elif hold == HOLD_MODE_HOLDUNTIL:
            data = {"hold": "on", "holdActivity": activity, "otmr": until}
        self._infinitude.callAPI("/api/config/zones/zone/{}/".format(self._zoneIndex), data)
