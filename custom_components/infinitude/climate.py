"""
Platform for exposing a Carrier Infinity Touch climate device through the
Infinitude proxy application
"""
from homeassistant.components.climate import (
    ClimateDevice,
    PLATFORM_SCHEMA,
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_TARGET_TEMPERATURE_HIGH, SUPPORT_TARGET_TEMPERATURE_LOW,
    SUPPORT_AWAY_MODE, SUPPORT_HOLD_MODE, SUPPORT_FAN_MODE,
    SUPPORT_OPERATION_MODE,
    STATE_AUTO, STATE_COOL, STATE_HEAT, STATE_IDLE, STATE_OFF, STATE_FAN_ONLY,
    ATTR_TARGET_TEMP_LOW, ATTR_TARGET_TEMP_HIGH)

from homeassistant.const import (
    CONF_HOST, CONF_PORT,
    ATTR_TEMPERATURE, TEMP_FAHRENHEIT)

import voluptuous as vol
import homeassistant.helpers.config_validation as cv

import urllib.request, urllib.parse, json, datetime

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

OPERATION_LIST = [STATE_AUTO, STATE_HEAT, STATE_COOL, STATE_OFF, STATE_FAN_ONLY]
FAN_LIST = [FAN_MODE_AUTO, FAN_MODE_HIGH, FAN_MODE_MED, FAN_MODE_LOW]

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_PORT): cv.port,
})

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the connection"""
    host = config.get(CONF_HOST)
    port = config.get(CONF_PORT)

    # Get the system status, and create devices for the enabled zones
    with urllib.request.urlopen("http://{}:{}/api/status.json".format(host, port)) as url:
        status = json.loads(url.read().decode())

    devices = []
    zones = status["zones"][0]["zone"]
    for zone in zones:
        if zone.get("enabled") == "on":
            devices.append(Infinitude(zone.get("@id"), config))
    add_devices(devices)

class Infinitude(ClimateDevice):
    def __init__(self, zoneID, config):
        self._zoneID = zoneID
        self._config = config
        self._statusSystem = {}
        self._statusZone = {}
        self._configSystem = {}
        self._configZone = {}
        self.update()

    def httpGetJson(self, path):
        req = urllib.request.Request("http://{}:{}{}".format(self._config.get("host"), self._config.get("port"), path))
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
        return data

    def httpPostJson(self, path, dataDict):
        params = json.dumps(dataDict).encode("utf-8")
        req = urllib.request.Request("http://{}:{}{}".format(self._config.get("host"), self._config.get("port"), path),
                                     data=params,
                                     headers={"content-type" : "application/json"})
        with urllib.request.urlopen(req) as response:
            responseData = response.read()
            if responseData is None or len(responseData) == 0:
                data = {}
            else:
                data = json.loads(responseData.decode())
        return data

    def update(self):
        self._statusSystem = self.httpGetJson("/api/status")["status"]
        for zone in self._statusSystem["zones"]["zone"]:
            if zone["@id"] == self._zoneID:
                self._statusZone = zone

        self._configSystem = self.httpGetJson("/api/system")["system"]["config"]
        for zone in self._configSystem["zones"]["zone"]:
            if zone["@id"] == self._zoneID:
                self._configZone = zone

        self._name = self._statusZone["name"]
        self._temperature = float(self._statusZone["rt"])
        self._setbackHeat = float(self._statusZone["htsp"])
        self._setbackCool = float(self._statusZone["clsp"])
        self._relativeHumidity = float(self._statusZone["rh"])
        self._operatingMode = self._configSystem["mode"]                # auto, heat, cool, off, fanonly
        self._hvacState = self._statusZone["zoneconditioning"]          # active_heat, active_cool, idle, more?
        self._fanMode = self._statusZone["fan"]                         # off, high, med, low
        self._currentActivity = self._statusZone["currentActivity"]     # home, away, sleep, wake, manual
        self._holdState = self._configZone["hold"]                      # on, off
        self._holdActivity = self._configZone["holdActivity"]           # home, away, sleep, wake, manual
        self._holdUntil = self._configZone["otmr"]                      # HH:MM (on the quarter-hour)

        # Iterate through the system config to calculate the current and next schedule details
        # Looks for the next 'enabled' period in the zone program
        self._scheduledActivity = None
        self._scheduledActivityStart = None
        self._nextActivity = None
        self._nextActivityStart = None
        dt = datetime.datetime.strptime(self._statusSystem["localTime"][:-6], "%Y-%m-%dT%H:%M:%S") # Strip the TZ offset, since this is already in local time
        while self._nextActivity is None:
            dayName = dt.strftime("%A")
            program = next((day for day in self._configZone["program"]["day"] if day["@id"] == dayName))
            for period in program["period"]:
                if period["enabled"] == "off":
                    continue
                periodHH, periodMM = period["time"].split(":")
                periodDt = datetime.datetime(dt.year, dt.month, dt.day, int(periodHH), int(periodMM))
                if periodDt < dt:
                    self._scheduledActivity = period["activity"]
                    self._scheduledActivityStart = periodDt
                if periodDt >= dt:
                    self._nextActivity = period["activity"]
                    self._nextActivityStart = periodDt
                    break
            dt = datetime.datetime(year=dt.year, month=dt.month, day=dt.day) + datetime.timedelta(days=1)
            #dt += datetime.timedelta(days=1)

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_FLAGS

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return TEMP_FAHRENHEIT

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    @property
    def device_state_attributes(self):
        """Return the device specific state attributes."""
        attributes = {
            "infinity_name": self._name,
            "infinity_temperature": self._temperature,
            "infinity_setbackHeat": self._setbackHeat,
            "infinity_setbackCool": self._setbackCool,
            "infinity_relativeHumidity": self._relativeHumidity,
            "infinity_operatingMode": self._operatingMode,
            "infinity_hvacState": self._hvacState,
            "infinity_fanMode": self._fanMode,
            "infinity_currentActivity": self._currentActivity,
            "infinity_holdState": self._holdState,
            "infinity_holdActivity": self._holdActivity,
            "infinity_holdUntil": self._holdUntil,
            "infinity_scheduledActivity": self._scheduledActivity,
            "infinity_scheduledActivityStart": self._scheduledActivityStart,
            "infinity_nextActivity": self._nextActivity,
            "infinity_nextActivityStart": self._nextActivityStart,
        }
        return attributes

    @property
    def name(self):
        """Return the name of the climate device."""
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
        return (self._holdActivity == "away" and self._holdState == "on" and self._holdUntil is None)

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
            self._setbackHeat = kwargs[ATTR_TARGET_TEMP_HIGH]
            data["htsp"] = kwargs[ATTR_TARGET_TEMP_HIGH]
        if ATTR_TARGET_TEMP_LOW in kwargs:
            self._setbackCool = kwargs[ATTR_TARGET_TEMP_LOW]
            data["clsp"] = kwargs[ATTR_TARGET_TEMP_LOW]

        # Update the 'manual' activity with the updated temperatures
        # Switch to the 'manual' activity and hold until the next scheduled activity
        self.httpPostJson("/api/config/zones/{}/activities/manual".format(self._zoneID), data)
        self.set_hold_mode(HOLD_MODE_HOLDUNTIL, activity="manual")

    def set_fan_mode(self, fan):
        """Set new fan mode."""
        # Translate 'auto' display value to config value
        if fan == FAN_MODE_AUTO:
            fan = "off"

        # Update the 'manual' activity with the selected fan mode, preserving the current setbacks
        # Switch to the 'manual' activity and hold until the next scheduled activity
        self.httpPostJson("/api/config/zones/{}/activities/manual".format(self._zoneID), {"fan" : fan, "htsp" : self._setbackHeat, "clsp" : self._setbackCool})
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
        self.httpPostJson("/api/config", data)

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
            data = {"hold": "off", "holdActivity": None, "otmr": None}
        elif hold == HOLD_MODE_HOLD:
            data = {"hold": "on", "holdActivity": activity, "otmr": None}
        elif hold == HOLD_MODE_HOLDUNTIL:
            data = {"hold": "on", "holdActivity": activity, "otmr": until}
        self.httpPostJson("/api/config/zones/{}".format(self._zoneID), data)