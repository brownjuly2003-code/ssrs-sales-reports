"""Create one daily SSRS email subscription. No network calls on import."""

import argparse
import json
import os
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

SERVICE = "http://schemas.microsoft.com/sqlserver/reporting/2010/03/01/ReportServer"
SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
# Basic authentication sends the password in clear text: HTTPS is required, except on the
# loopback interface of the report server itself (the Windows runner of the project).
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
ET.register_namespace("soap", SOAP)
ET.register_namespace("rs", SERVICE)


def node(parent, name, value=None):
    element = ET.SubElement(parent, f"{{{SERVICE}}}{name}")
    element.text = value
    return element


def pair(parent, name, value):
    item = node(parent, "ParameterValue")
    node(item, "Name", name)
    node(item, "Value", value)


def create_subscription(config, send):
    params = config["parameters"]
    expected = {"StartDate", "EndDate", "RegionIds", "StoreIds", "CategoryIds"}
    if set(params) != expected or any(not isinstance(v, list) or not v for v in params.values()):
        raise ValueError("Supply all five report parameters")
    if (
        len(params["StartDate"]) != 1
        or len(params["EndDate"]) != 1
        or date.fromisoformat(params["StartDate"][0]) > date.fromisoformat(params["EndDate"][0])
    ):
        raise ValueError("Invalid period")
    if config["format"] not in {"PDF", "EXCELOPENXML"}:
        raise ValueError("Unsupported attachment format")
    if not config["report"].startswith("/") or not config["to"].strip():
        raise ValueError("Missing report path or recipient")

    schedule = ET.Element("ScheduleDefinition")
    ET.SubElement(schedule, "StartDateTime").text = datetime.fromisoformat(
        config["start_time"]
    ).isoformat()
    ET.SubElement(ET.SubElement(schedule, "DailyRecurrence"), "DaysInterval").text = "1"
    envelope = ET.Element(f"{{{SOAP}}}Envelope")
    body = ET.SubElement(envelope, f"{{{SOAP}}}Body")
    request = node(body, "CreateSubscription")
    node(request, "ItemPath", config["report"])
    extension = node(request, "ExtensionSettings")
    node(extension, "Extension", "Report Server Email")
    settings = node(extension, "ParameterValues")
    for name, value in {
        "TO": config["to"],
        "RenderFormat": config["format"],
        "IncludeReport": "True",
        "Subject": "Retail sales and closing inventory",
    }.items():
        pair(settings, name, value)
    node(request, "Description", "Daily retail sales report")
    node(request, "EventType", "TimedSubscription")
    node(request, "MatchData", ET.tostring(schedule, encoding="unicode"))
    arguments = node(request, "Parameters")
    for name, values in params.items():
        for value in values:
            pair(arguments, name, str(value))
    reply = ET.fromstring(
        send(
            SERVICE + "/CreateSubscription",
            ET.tostring(envelope, encoding="utf-8", xml_declaration=True),
        )
    )
    fault = reply.find(".//{*}Fault")
    if fault is not None:
        raise ValueError(fault.findtext("{*}faultstring") or "SSRS SOAP fault")
    result = reply.findtext(".//{*}CreateSubscriptionResponse/{*}SubscriptionID")
    if not result:
        raise ValueError("SSRS returned no subscription ID")
    return result


def check_endpoint(url):
    parts = urlparse(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK):
        return url
    raise ValueError(
        "Use an HTTPS ReportService2010 endpoint with Basic authentication"
        " (plain HTTP is accepted only for localhost)"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    endpoint = check_endpoint(os.environ["SSRS_SOAP_URL"])

    def send(action, body):
        response = requests.post(
            endpoint,
            data=body,
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{action}"'},
            auth=(os.environ["SSRS_USER"], os.environ["SSRS_PASSWORD"]),
            timeout=30,
            allow_redirects=False,
        )
        if response.status_code != 500:
            response.raise_for_status()
        return response.content

    print(create_subscription(json.loads(args.config.read_bytes()), send))


if __name__ == "__main__":
    main()
