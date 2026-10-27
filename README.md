# UniFi Network

A [labdex](https://github.com/labdex-app/labdex) plugin. Clients of a UniFi Network controller into Discovery (address, MAC, name), and UniFi devices (switches, access points, gateways) as host status. With a local account it also reads which port each device, client and LLDP neighbour sits on, as suggested cables. Read-only. Signs in with a local account (UniFi OS Server, consoles, the Network application on its own), a console's API key, or a Site Manager key from unifi.ui.com.

**Version** 2.1 · **Works with** labdex >=1.9,<2 · **Plugin id** `unifi`

> [!WARNING]
> Built with AI, for home labs only: labdex and its plugins were written largely with an AI
> coding assistant (Claude), directed and reviewed by a human. They haven't had an independent
> security audit and may contain security problems. Run them on a trusted home network.

## Install

It ships with labdex: **Plugins › Marketplace**, then *Install* (nothing is downloaded). Updates arrive through the official Marketplace (signed with the labdex project key).

## Settings

| Setting | Notes |
| --- | --- |
| Sign in with | A local, view-only account is enough. Accounts with two-factor sign-in don't work here. |
| Controller URL | UniFi OS Server: port 11443. Consoles (UDM, UCG, Cloud Key): port 443. Network application on its own: port 8443. |
| Username | UniFi OS: Admins & Users → add an admin, “Restrict to local access only”, role View Only for Network |
| Password |  |
| API key | Console: Network → Settings → Control Plane → Integrations. Site Manager: unifi.ui.com → API → Create API key. |
| Consoles | Names or IDs of the consoles to read; empty = every console the key sees |
| Site Manager URL | Leave as it is |
| Check the TLS certificate | UniFi uses a self-signed certificate unless you replaced it. Site Manager is always checked. |
| Sites | Names of the sites to read; empty = all |
| API path | Console: proxy/network/integration/v1. Network application on its own: integration/v1 |

## Actions

- **Check connection** (run)
- **Read clients and devices** (run)

## Releasing

Bump `version` in `plugin.yaml`, add a `## <version>` section to `CHANGELOG.md`, commit, then
publish a GitHub release tagged `v<version>` (web UI, or `gh release create v<version> --target main --notes ""`).
The release workflow signs the plugin with the `LABDEX_SIGNING_KEY` secret, attaches the zip and
`CHANGELOG.md` to the release and puts that version's CHANGELOG section into its description.
Files listed in `.releaseignore` stay out of the zip.
