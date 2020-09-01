# homeassistant-infinitude
Home Assistant custom component for controlling Carrier Infinity Touch thermostats through an [Infinitude](https://github.com/nebulous/infinitude) proxy server.

# Installation Instructions

## Install Manually

1. Create a `custom_components` directory within your Home Assistant `config` directory if it does not already exist.

2. [Download this repository](https://github.com/MizterB/homeassistant-infinitude/archive/master.zip).

3. Copy `custom_components/infinitude` from the repository into Home Assistant's `custom_components` directory.

4. Follow the configuration instructions.

## Install with HACS
This custom component can be integrated into [HACS](https://github.com/hacs/integration), so you can track future updates.  Please see [their guide](https://hacs.xyz/docs/installation/manual) for the HACS installation process.

1. Select HACS from the left-hand navigation menu in the Home Assistant web UI.

2. Click _Integrations_.

3. Click the three dots in the upper right-hand corner and select _Custom Repositories_.

4. Paste "https://github.com/MizterB/homeassistant-infinitude" into _Add custom repository URL_.

5. Select "Integration" from the _Category_ dropdown and click _Add_.

6. Close the Custom repositories dialog after it updates with the new integration.

7. "Carrier Infinity via Inifinitude Proxy" will appear in your list of repositories.  Click "Install" inside it.

8. Click "Install" inside the dialog box.

9. Follow the configuration instructions.

# Configuration
Add the following to your `configuration.yaml`:
```yaml
climate:
  - platform: infinitude
    host: <infinitude_hostname_or_ip>
    port: <optional, defaults to 3000>
    zone_names:
      - Custom Zone Name 1
      - 
      - Custom Zone Name 3
      - ...
```
Custom zone names are optional, and are applied in ascending order (zones 1-8).  If a blank name is provided (like in the second entry above), the zone name is retrieved from the thermostat itself.


## Changelog
*0.7.1*
- Extend ClimateEntity, rather than ClimateDevice
  
*0.7*
- Submit changes via POST to be compatible with latest Infinitude API ([see commit](https://github.com/MizterB/infinitude/commit/a0c3b7a58c1c3535a0811001bcfed2c43c672906))
- Handle timezone offsets being inconsistently passed in localTime.
- Make custom zone names optional, and with ability to only override specific zones

*0.6*
- Rewritten for compatibility with the new climate spec in HA .96
- New presets available to quickly change activities and manage hold settings:
  - 'Scheduled' preset restores the currently scheduled activity
  - 'Activity' presets override the currently scheduled activity until the next schedule change
  - 'Override' preset holds any setting changes until the next schedule change (automatically enabled on temperature & fan changes)
  - 'Hold' preset holds any setting changes indefinitely
- Service set_hold_mode is mostly replaced by presets, but can still be used for setting specific 'hold until' times

*0.5*
- New service 'infinitude.set_hold_mode' enables changing activities and corresponding hold settings.

*0.4*
- Added manifest.json
- Fixed temperature setting reversal while on Auto mode(thanks @ccalica!)

*0.3*
- Safely handle updates of values that might not exist on all thermostats
- Provide ability to override zone names

*0.2*
- Updated constants to be compatible with HA .89

*0.1*
- Initial release
