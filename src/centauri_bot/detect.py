# -*- coding: utf-8 -*-
"""Which firmware answers at this address: stock Elegoo or OpenCentauri/COSMOS.

Both live on the same printer and the same IP, so the only honest way to tell
them apart is to knock and see who answers. The two are mutually exclusive in
practice: flashing COSMOS replaces the stock stack, and the stock stack has no
Moonraker at all.

Moonraker is probed first because its answer is positive proof - a JSON reply
from /printer/info can come from nothing else. The stock check is weaker (an
open TCP port), so it only gets to speak when Moonraker stayed silent.
"""
from . import backend
from . import moonraker
from . import sdcp

# COSMOS serves Moonraker through nginx on 80; a bare Moonraker listens on 7125.
MOONRAKER_PORTS = (80, 7125)


def moonraker_url_for(host, port):
    return "http://%s" % host if port == 80 else "http://%s:%d" % (host, port)


def probe_moonraker(host, timeout=3, api_key="", client_factory=None):
    """Base URL of a Moonraker that answers, or "" if none does."""
    factory = client_factory or moonraker.Client
    for port in MOONRAKER_PORTS:
        if not sdcp.tcp_reachable(host, port, timeout):
            continue
        url = moonraker_url_for(host, port)
        try:
            info = factory(url, api_key=api_key, timeout=timeout).printer_info()
        except Exception:
            continue
        if isinstance(info, dict) and info:
            return url
    return ""


def probe_sdcp(host, timeout=3):
    """Is the stock SDCP status port open?"""
    return sdcp.tcp_reachable(host, sdcp.WS_PORT, timeout)


AUTO = "auto"


def resolve(cfg, log=None, client_factory=None):
    """Turn "backend: auto" into a concrete backend by asking the printer.

    Kept out of config loading on purpose: reading a file must not touch the
    network. Nothing is guessed - if the printer stays silent we raise, so the
    service restarts and tries again instead of running against the wrong
    protocol and reporting nonsense.
    """
    if str(cfg.get("backend", "")).strip().lower() != AUTO:
        return cfg
    name, url, note = detect(
        cfg.get("printer_ip") or "",
        timeout=float(cfg.get("moonraker_timeout_sec", 5) or 5),
        api_key=cfg.get("moonraker_api_key", ""),
        client_factory=client_factory)
    if log:
        log(note)
    if not name:
        raise LookupError(note)
    resolved = dict(cfg)
    resolved["backend"] = name
    if name == backend.MOONRAKER and url and not cfg.get("moonraker_url"):
        resolved["moonraker_url"] = url
    return resolved


def detect(host, timeout=3, api_key="", client_factory=None):
    """Return (backend name, moonraker url, human explanation).

    An empty backend name means "could not tell" - the caller must ask rather
    than guess. Picking the wrong protocol would leave the user with a bot that
    connects to nothing and no hint why.
    """
    url = probe_moonraker(host, timeout, api_key, client_factory)
    if url:
        return (backend.MOONRAKER, url,
                "Moonraker ответил по адресу %s — это OpenCentauri/COSMOS." % url)
    if probe_sdcp(host, timeout):
        return (backend.SDCP, "",
                "Открыт порт %d, Moonraker не отвечает — это штатная прошивка "
                "Elegoo." % sdcp.WS_PORT)
    return ("", "",
            "Принтер не ответил ни как COSMOS (порты %s), ни как штатная "
            "прошивка (порт %d). Проверь адрес и что принтер включён."
            % (", ".join(str(p) for p in MOONRAKER_PORTS), sdcp.WS_PORT))
