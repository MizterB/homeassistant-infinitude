# homeassistant-infinitude
Home Assistant custom component for controlling Carrier Infinity Touch thermostats through an [Infinitude](https://github.com/nebulous/infinitude) proxy server.

# Installation Instructions

## Install Manually

1. Create a `custom_components` directory within your Home Assistant `config` directory if it does not already exist.

2. [Download this repository](https://github.com/MizterB/homeassistant-infinitude/archive/master.zip).

3. Copy `custom_components/infinitude` from the repository into Home Assistant's `custom_components` directory.

4. Follow the configuration instructions.

## Install with custom_updater
_These instructions still need to be validated_

This custom entity row can be integrated into [custom_updater](https://github.com/custom-components/custom_updater), so you can track future updates.  

As this is not included in the default card list, add the following to your `configuration.yaml`:

```yaml
custom_updater:
  component_urls:
    - https://raw.githubusercontent.com/MizterB/homeassistant-infinitude/master/custom_components.json
```

# Configuration

```yaml
climate:
  - platform: infinitude
    host: <infinitude_hostname_or_ip>
    port: <optional, defaults to 3000>
```

## Changelog

*0.1*
- Initial release