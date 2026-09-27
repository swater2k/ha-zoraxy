<p align="center">
  <img src="custom_components/zoraxy/brand/icon@2x.png" alt="Zoraxy integration icon" width="128">
</p>

<h1 align="center">Zoraxy for Home Assistant</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/swater2k/ha-zoraxy/releases"><img src="https://img.shields.io/github/v/release/swater2k/ha-zoraxy" alt="Release"></a>
</p>

A custom integration for the [Zoraxy](https://github.com/tobychui/zoraxy) reverse proxy: proxy hosts with upstream status, latency and availability, traffic statistics, certificates and access rules — plus optional control of hosts, the proxy, access rules, stream proxies, redirects and certificate renewal.

> [!NOTE]
> Community project, not affiliated with the Zoraxy project.

## How it connects

Zoraxy has no token-based API. The integration talks to the same internal API as the web interface: it loads the login page for the CSRF token, signs in with username and password and keeps the session cookie. When the session expires or Zoraxy restarts, it signs in again automatically.

This has two consequences:

- The integration uses a **Zoraxy admin account**. Zoraxy has no restricted roles.
- The internal API is not a stable public interface. A Zoraxy update can change it; the integration then reports an error instead of wrong values.

## Features

- **Proxy hosts**: one device per host with upstream online status, latency and availability from Zoraxy's uptime monitor, upstream address and enabled state
- **Traffic**: requests, errors and unique visitors today, received and sent data, active connections
- **Certificates**: expiry date per certificate, expired certificates
- **Access rules**: blacklist and whitelist state with the listed IP addresses and countries
- **System**: version, start time, CPU, memory and disk usage
- **Optional control**, each turned on separately: switches for proxy hosts, the whole proxy, blacklist and whitelist, stream proxies and redirects, a button to renew certificates and actions to block and unblock IP addresses
- Fully local polling; the large uptime history is read less often than the rest

## Requirements

- Home Assistant **2026.2** or newer
- Zoraxy with the web interface enabled (tested with **3.3.5**)
- Network access from Home Assistant to the Zoraxy web interface (default port `8000`)

## Installation

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/swater2k/ha-zoraxy`, type **Integration**
2. Download **Zoraxy** and restart Home Assistant
3. **Settings → Devices & services → Add integration → Zoraxy**

Manual alternative: copy `custom_components/zoraxy` into your `custom_components` folder and restart.

## Setup

| Field | Default | Description |
|---|---|---|
| Zoraxy URL | – | Address of the web interface, for example `http://192.168.1.10:8000`. Without a scheme, `http` and port `8000` are assumed. |
| Username / password | – | Zoraxy admin account |
| Verify SSL certificate | on | Only relevant for `https` |

Use the **internal address** of the web interface. A domain that is itself proxied through Zoraxy or protected by forward auth will not work.

The Zoraxy host ID is used as unique ID, so a changed IP address can be fixed with **Reconfigure** without losing entities.

### Options

| Option | Default | Description |
|---|---|---|
| Polling interval | `60 s` | Hosts, statistics, certificates and access rules (15–900 s) |
| Uptime interval | `300 s` | Uptime monitor (60–3600 s). Zoraxy checks upstreams every 5 minutes and returns a whole day of history per request. |
| Control: proxy hosts | off | One switch per proxy host |
| Control: whole proxy | off | A switch that stops or starts the whole reverse proxy |
| Control: access rules | off | Blacklist and whitelist switches, actions block and unblock IP |
| Control: stream proxies | off | One switch per TCP/UDP stream proxy |
| Control: redirects | off | One switch per redirect rule |
| Control: certificates | off | Button to start the ACME renewal |

## Entities

Entities marked ✗ are disabled by default.

### Zoraxy

| Entity | Type | Default |
|---|---|---|
| Proxy running | binary sensor (running) | ✓ |
| Requests today, errors today, unique visitors today | sensor | ✓ |
| Successful requests today | sensor | ✗ |
| Received today, sent today | sensor (data size) | ✓ |
| Active connections, proxy hosts, expired certificates | sensor | ✓ |
| Certificate *domain* — expiry, remaining days as attribute | sensor (timestamp) | ✓ |
| Blacklist / whitelist *rule* — IPs and countries as attributes | binary sensor (diagnostic), switch with control option | ✓ |
| Stream *name*, redirect *url* | binary sensor, switch with control option | ✓ |
| Version, started, CPU, memory, disk usage | sensor (diagnostic) | ✓ |
| Reverse proxy | switch | only with control option |
| Renew certificates | button | only with control option |

### Per proxy host

| Entity | Type | Default |
|---|---|---|
| Upstream online — unavailable for disabled hosts or when the monitor is off | binary sensor (connectivity) | ✓ |
| Enabled | binary sensor, switch with control option | ✓ |
| Latency, availability over the returned history | sensor | ✓ |
| Upstream | sensor (diagnostic) | ✓ |
| Status code, last check | sensor (diagnostic) | ✗ |

## Actions

Both need the option **Control: access rules**.

| Action | Description |
|---|---|
| `zoraxy.block_ip` | Add an IP address or CIDR range to the blacklist of an access rule (default `default`), with an optional comment |
| `zoraxy.unblock_ip` | Remove it again |

The blacklist of the rule has to be enabled for a block to take effect.

```yaml
action: zoraxy.block_ip
data:
  ip: 203.0.113.7
  comment: Blocked by Home Assistant
```

## Removal

1. **Settings → Devices & services → Zoraxy → ⋮ → Delete**
2. Remove the repository in HACS and restart Home Assistant

## Troubleshooting

- **"Zoraxy not reachable"**: check address and port, and that the address points to the web interface itself and not to a proxied domain.
- **"Username or password rejected"**: check the credentials by signing in to the web interface.
- **Upstream online is unavailable**: the host is disabled, its uptime monitor is turned off in Zoraxy, or the first check has not run yet.
- **Diagnostics**: Settings → Devices & services → Zoraxy → ⋮ → Download diagnostics. URL, credentials and IP lists are redacted.

```yaml
logger:
  logs:
    custom_components.zoraxy: debug
```

## License

[MIT](LICENSE)
