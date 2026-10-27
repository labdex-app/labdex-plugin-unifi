"""UniFi Network: clients into Discovery, UniFi devices as host status.

Four ways in, one result:

    apikey    UniFi OS console, Network 9.3+: the integration API with a local API key
    password  UniFi OS console or UniFi OS Server: a local account, cookie + CSRF token,
              the Network application's own API under /proxy/network
    classic   the Network application on its own (port 8443): /api/login, same API
    cloud     Site Manager (unifi.ui.com) API key: consoles from api.ui.com, each read
              through the cloud connector; devices only, if a console can't be reached

Every source hands back the same shapes, so the poll below doesn't care which one it is:
    site    {"key", "name"}
    client  {"ip", "mac", "name"}
    device  {"name", "model", "ip", "mac", "online"}
    link    what cabling.record takes: a device port and its neighbour (LLDP tables, uplinks,
            wired clients' switch ports). Only the Network application's own API has these
            (sign-in with a local account); the integration API doesn't tell ports.
"""
from __future__ import annotations

from labdex.plugins.base import Plugin as Base
from labdex.plugins.base import PluginError

INTEGRATION = "proxy/network/integration/v1"
CLOUD = "https://api.ui.com"
MFA = "Ubic2faTokenRequired"


def _paged(http, path, params=None):
    """The integration API pages with offset/limit and says how many there are."""
    out, offset = [], 0
    while True:
        d = http.get(path, params={**(params or {}), "offset": offset, "limit": 200}).json()
        items = d.get("data") or []
        out += items
        offset += len(items)
        if not items or offset >= int(d.get("totalCount") or 0):
            return out


def _integration_client(c):
    return {"ip": c.get("ipAddress") or "", "mac": (c.get("macAddress") or "").lower(), "name": c.get("name") or ""}


def _integration_device(d):
    return {"name": d.get("name") or "", "model": d.get("model") or "", "ip": d.get("ipAddress") or "",
            "mac": (d.get("macAddress") or "").lower(), "online": (d.get("state") or "").upper() == "ONLINE"}


class _Integration:
    """The integration API (v1), on the console itself or through the cloud connector."""

    def __init__(self, http, prefix=""):
        self.http, self.prefix = http, prefix.strip("/")

    def _p(self, path):
        return f"{self.prefix}/{path}" if self.prefix else path

    def sites(self):
        return [{"key": s["id"], "name": s.get("name") or s.get("internalReference") or s["id"],
                 "ref": s.get("internalReference") or ""} for s in _paged(self.http, self._p("sites"))]

    def clients(self, site):
        return [_integration_client(c) for c in _paged(self.http, self._p(f"sites/{site['key']}/clients"))]

    def devices(self, site):
        return [_integration_device(d) for d in _paged(self.http, self._p(f"sites/{site['key']}/devices"))]


class _Classic:
    """The Network application's own API (what its web UI uses), behind a login."""

    def __init__(self, http, prefix):
        self.http, self.prefix = http, prefix.strip("/")

    def _get(self, path):
        d = self.http.get(f"{self.prefix}/{path}").json()
        if (d.get("meta") or {}).get("rc") not in (None, "ok"):
            raise PluginError(f"{path}: {(d.get('meta') or {}).get('msg') or 'the controller said no'}")
        return d.get("data") or []

    def sites(self):
        return [{"key": s["name"], "name": s.get("desc") or s["name"], "ref": s["name"]} for s in self._get("api/self/sites")]

    def clients(self, site):
        return [{"ip": c.get("ip") or c.get("last_ip") or "", "mac": (c.get("mac") or "").lower(),
                 "name": c.get("name") or c.get("hostname") or ""} for c in self._get(f"api/s/{site['key']}/stat/sta")]

    def devices(self, site):
        return [{"name": d.get("name") or d.get("mac") or "", "model": d.get("model") or "", "ip": d.get("ip") or "",
                 "mac": (d.get("mac") or "").lower(), "online": d.get("state") == 1}
                for d in self._get(f"api/s/{site['key']}/stat/device")]

    def links(self, site):
        """What's plugged in where: each device's LLDP table and uplink, and wired clients' switch ports."""
        devs = self._get(f"api/s/{site['key']}/stat/device")
        by_mac = {(d.get("mac") or "").lower(): d for d in devs}

        def me(d):
            return {"name": d.get("name") or d.get("mac") or "", "ip": d.get("ip") or "", "mac": (d.get("mac") or "").lower()}

        def port(d, idx):
            if not idx:
                return ""
            p = next((p for p in (d or {}).get("port_table") or [] if p.get("port_idx") == idx), {})
            return p.get("name") or f"Port {idx}"

        out = []
        for d in devs:
            for nb in d.get("lldp_table") or []:
                local = nb.get("local_port_name") or port(d, nb.get("local_port_idx"))
                cid = (nb.get("chassis_id") or "").lower()
                out.append({"device": me(d), "port": local, "protocol": "lldp", "remote_port": nb.get("port_id") or nb.get("port_descr") or "",
                            "remote": {"name": nb.get("system_name") or nb.get("chassis_name") or "", "ip": nb.get("management_ip") or "",
                                       "mac": cid if cid.count(":") == 5 else "", "platform": nb.get("chassis_descr") or ""}})
            up = d.get("uplink") or {}
            if up.get("uplink_mac") and up.get("type", "wire") == "wire" and up.get("port_idx"):
                peer = by_mac.get(up["uplink_mac"].lower())
                out.append({"device": me(d), "port": port(d, up["port_idx"]), "protocol": "unifi",
                            "remote_port": port(peer, up.get("uplink_remote_port")) if peer else (f"Port {up['uplink_remote_port']}" if up.get("uplink_remote_port") else ""),
                            "remote": me(peer) if peer else {"mac": up["uplink_mac"].lower()}})
        for c in self._get(f"api/s/{site['key']}/stat/sta"):
            sw = by_mac.get((c.get("sw_mac") or "").lower())
            if c.get("is_wired") and sw and c.get("sw_port"):
                out.append({"device": me(sw), "port": port(sw, c["sw_port"]), "protocol": "unifi", "remote_port": "",
                            "remote": {"name": c.get("name") or c.get("hostname") or "", "ip": c.get("ip") or c.get("last_ip") or "",
                                       "mac": (c.get("mac") or "").lower()}})
        return out


class Plugin(Base):
    # ------------------------------------------------------------------ connecting
    @property
    def auth(self):
        return self.settings.get("auth") or "apikey"

    def _http(self, base, headers=None):
        verify = True if self.auth == "cloud" else bool(self.settings.get("verify_tls", False))
        return self.ctx.http(base, verify=verify, timeout=20, headers={"Accept": "application/json", **(headers or {})})

    def _url(self):
        url = (self.settings.get("url") or "").rstrip("/")
        if not url:
            raise PluginError("set the controller URL first")
        return url

    def _login(self, http, path, csrf_cookie=None):
        s = self.settings
        if not s.get("username") or not s.get("password"):
            raise PluginError("set the username and password first")
        try:
            r = http.post(path, json={"username": s["username"], "password": s["password"], "remember": False})
        except PluginError as exc:
            msg = str(exc)
            if MFA in msg or ": HTTP 499" in msg:
                raise PluginError("this account has two-factor sign-in; use a local account without it "
                                  "(UniFi OS: Admins → add an admin with “Restrict to local access only”, role View Only)") from exc
            if "access denied" in msg or "HTTP 400" in msg:
                raise PluginError("the controller didn't accept the username or password (a local account, "
                                  "not a UI.com account with two-factor)") from exc
            raise
        token = r.headers.get("X-CSRF-Token") or (http.s.cookies.get(csrf_cookie) if csrf_cookie else None)
        if token:
            http.s.headers["X-CSRF-Token"] = token  # only writes need it; labdex sends it anyway
        return r

    def _logout(self, http, path):
        try:
            http.post(path, json={})
        except PluginError:
            pass  # the session times out by itself

    def _sources(self):
        """[(label, source, close)] — one per console (only the cloud has more than one)."""
        a = self.auth
        if a == "apikey":
            if not self.settings.get("api_key"):
                raise PluginError("set the API key first")
            http = self._http(self._url(), {"X-API-KEY": self.settings["api_key"]})
            return [("", _Integration(http, self.settings.get("path_prefix") or INTEGRATION), None)]
        if a == "password":
            http = self._http(self._url())
            self._login(http, "api/auth/login")
            return [("", _Classic(http, "proxy/network"), lambda: self._logout(http, "api/auth/logout"))]
        if a == "classic":
            http = self._http(self._url())
            self._login(http, "api/login", csrf_cookie="csrf_token")
            return [("", _Classic(http, ""), lambda: self._logout(http, "api/logout"))]
        if a == "cloud":
            return self._cloud()
        raise PluginError(f"unknown sign-in “{a}”")

    # ----------------------------------------------------------------------- cloud
    def _cloud_http(self):
        if not self.settings.get("api_key"):
            raise PluginError("set the Site Manager API key first (unifi.ui.com → API)")
        return self._http(self.settings.get("cloud_url") or CLOUD, {"X-API-KEY": self.settings["api_key"]})

    def _cloud_list(self, http, path):
        """Site Manager pages with nextToken."""
        out, token = [], None
        while True:
            d = http.get(path, params={"pageSize": 200, **({"nextToken": token} if token else {})}).json()
            out += d.get("data") or []
            token = d.get("nextToken")
            if not token:
                return out

    def _consoles(self, http):
        want = {x.lower() for x in self.settings.get("consoles") or []}
        out = []
        for h in self._cloud_list(http, "v1/hosts"):
            rs = h.get("reportedState") or {}
            name = rs.get("name") or rs.get("hostname") or h.get("id", "")
            if want and name.lower() not in want and str(h.get("id", "")).lower() not in want:
                continue
            out.append({"id": h["id"], "name": name})
        return out

    def _cloud(self):
        http = self._cloud_http()
        consoles = self._consoles(http)
        if not consoles:
            raise PluginError("the API key sees no consoles" + (" with those names" if self.settings.get("consoles") else ""))
        return [(c["name"], _Integration(http, f"v1/connector/consoles/{c['id']}/{INTEGRATION}"), None) for c in consoles]

    def _cloud_devices(self):
        """Without the connector: devices and their state straight from Site Manager."""
        http = self._cloud_http()
        names = {c["id"]: c["name"] for c in self._consoles(http)}
        out = []
        for host in self._cloud_list(http, "v1/devices"):
            if host.get("hostId") not in names:
                continue
            for d in host.get("devices") or []:
                out.append({"name": d.get("name") or "", "model": d.get("model") or d.get("shortname") or "", "ip": d.get("ip") or "",
                            "mac": (d.get("mac") or "").lower(), "online": (d.get("status") or "").lower() == "online",
                            "site": names[host["hostId"]]})
        return out

    # ----------------------------------------------------------------------- reading
    def _sites(self, src):
        want = {x.lower() for x in self.settings.get("sites") or []}
        return [s for s in src.sites() if not want or s["name"].lower() in want or s["ref"].lower() in want]

    def test(self):
        found = []
        for label, src, close in self._sources():
            try:
                sites = self._sites(src)
            finally:
                close and close()
            found.append((label, sites))
        if len(found) == 1:
            sites = found[0][1]
            return f"{len(sites)} site(s): " + ", ".join(s["name"] for s in sites)
        return f"{len(found)} consoles. " + "; ".join(f"{label}: " + ", ".join(s["name"] for s in sites) for label, sites in found)

    def _read(self):
        clients, devices, notes = [], [], []
        self._links = []
        for label, src, close in self._sources():
            try:
                for site in self._sites(src):
                    where = f"{label} / {site['name']}" if label else site["name"]
                    clients += src.clients(site)
                    devices += [d | {"site": where} for d in src.devices(site)]
                    if hasattr(src, "links"):
                        self._links += src.links(site)
            except PluginError as exc:
                if self.auth != "cloud":
                    raise
                notes.append((label, str(exc)))
            finally:
                close and close()
        if notes:
            # a console the connector can't reach (older firmware, or offline): its devices still come from Site Manager
            self.ctx.log("the cloud connector couldn't read " + "; ".join(f"{a}: {b}" for a, b in notes)
                         + " — its devices come from Site Manager, without clients")
            unread = {a for a, _ in notes}
            devices += [d for d in self._cloud_devices() if d["site"] in unread]
        return clients, devices, notes

    def run_poll(self, params):
        clients, devices, notes = self._read()
        entries = [{"ip": c["ip"], "mac": c["mac"], "hostname": c["name"]} for c in clients if c["ip"]]
        n = self.ctx.discovery.record_neighbours(f"unifi {self.ctx.name}", entries)
        seen = self.ctx.discovery.record_links(f"unifi {self.ctx.name}", self._links) if self._links or self.auth in ("password", "classic") else None
        match = self.ctx.match_host()
        report = {}
        for d in devices:
            h = match(d["ip"]) or match(d["name"])
            if h:
                report[h] = {"state": "up" if d["online"] else "down",
                             "detail": f"{d['model']} {'online' if d['online'] else 'offline'}".strip(),
                             "data": {"model": d["model"], "site": d["site"]}}
        self.ctx.report_status(report)
        self.ctx.set_state("devices", [{"name": d["name"], "model": d["model"], "ip": d["ip"],
                                        "state": "ONLINE" if d["online"] else "OFFLINE", "site": d["site"]} for d in devices])
        out = {"clients": len(clients), "recorded": n, "devices": len(devices), "matched": len(report)}
        if seen is not None:
            out["links"] = seen["links"]
        if notes:
            out["unreachable"] = len(notes)
        return out

    def widget_devices(self, host):
        devs = self.ctx.state.get("devices")
        if devs is None:
            return {"kind": "list", "items": [], "sub": "run “Read clients and devices”"}
        match = self.ctx.match_host()
        items = []
        for d in sorted(devs, key=lambda x: (x["state"].upper() == "ONLINE", x["name"])):
            h = match(d["ip"]) or match(d["name"])
            items.append({"label": d["name"] or d["ip"], "state": "up" if d["state"].upper() == "ONLINE" else "down",
                          "detail": d["model"] + ("" if h else " · not in the inventory"), "link": f"/hosts/{h}" if h else ""})
        off = sum(1 for d in devs if d["state"].upper() != "ONLINE")
        return {"kind": "status", "items": items[:25], "sub": f"{len(devs)} devices, {off} offline"}
