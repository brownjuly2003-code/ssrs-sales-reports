"""Offline structure checks of .github/workflows/ssrs-windows.yml (nothing is executed here).

The workflow runs only on a GitHub-hosted Windows runner; these tests read its text.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ssrs-windows.yml"


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def job(workflow):
    assert list(workflow["jobs"]) == ["ssrs"]
    return workflow["jobs"]["ssrs"]


def runs(job):
    return [step["run"].replace("\\", "/") for step in job["steps"] if "run" in step]


def test_only_a_manual_start(workflow):
    # YAML 1.1 reads the key "on" as True.
    trigger = workflow.get("on", workflow.get(True))
    assert trigger == {"workflow_dispatch": None}


def test_windows_runner_with_limits(workflow, job):
    assert job["runs-on"] == "windows-2022"
    assert 60 <= job["timeout-minutes"] <= 120
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert workflow["permissions"] == {"contents": "read"}
    assert job["defaults"]["run"]["shell"] == "powershell"


def test_actions_are_pinned_by_commit(job):
    used = [step["uses"] for step in job["steps"] if "uses" in step]
    assert len(used) == 3
    for action in used:
        assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", action), action


def test_every_run_stops_on_errors_and_checks_native_exit_codes(job):
    for text in runs(job):
        assert "$ErrorActionPreference = 'Stop'" in text or "exit 0" in text, text[:80]
        for line in text.splitlines():
            if (
                re.match(r"\s*(\$\w+ = )?(python|sqlcmd|curl\.exe|icacls) ", line)
                and "| Out-Null" not in line
            ):
                assert "$LASTEXITCODE" in text, line


def test_passwords_are_generated_masked_and_not_in_the_repository(workflow, job):
    first = runs(job)[0]
    assert "RandomNumberGenerator" in first and "::add-mask::" in first
    for name in ("SA_PASSWORD", "READER_PASSWORD", "CLIENT_PASSWORD"):
        assert name not in job["env"], name
        assert name in first, name
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "Replace($env:SA_PASSWORD, '***')" in text


def test_steps_call_the_project_scripts(job):
    text = "\n".join(runs(job))
    for needed in (
        "python -m ssrs_lab generate",
        "python -m ssrs_lab reference",
        "scripts/Select-SqlDataVolume.ps1",
        "scripts/Initialize-ReportServer.ps1",
        "sql/01-schema.sql",
        "sql/02-load.sql",
        "sql/03-datasets.sql",
        "tests/test_mssql.py",
        "sql/grant_report_reader.sql",
        "scripts/Publish-Report.ps1",
        "rdl/SalesStock.rdl",
        "scripts/Export-Report.ps1",
        "scripts/check_render.py",
        "scripts/check_server.py header",
        "scripts/Get-ReportParameters.ps1",
        "scripts/check_server.py cascade",
        "scripts/smtp_sink.py",
        "python -m ssrs_lab.subscription subscription/windows-runner.json",
        "msdb.dbo.sp_start_job",
        "scripts/check_server.py mail",
        "tests/test_check_render.py",
        "tests/test_check_server.py",
    ):
        assert needed in text, needed
        path = needed.split()[0] if "/" in needed.split()[0] else needed.split()[-1]
        if path.startswith(("scripts/", "sql/", "tests/", "rdl/", "subscription/")):
            assert (ROOT / path).is_file(), needed
    order = (
        "scripts/Select-SqlDataVolume.ps1",
        "scripts/Initialize-ReportServer.ps1",
        "sql/01-schema.sql",
        "scripts/Publish-Report.ps1",
        "scripts/Export-Report.ps1",
        "scripts/Get-ReportParameters.ps1",
        "python -m ssrs_lab.subscription",
        "msdb.dbo.sp_start_job",
        "scripts/check_server.py mail",
    )
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions)


def test_setup_installs_engine_agent_and_reporting_services(job):
    text = "\n".join(runs(job))
    assert "'/FEATURES=SQLENGINE', '/INSTANCENAME=MSSQLSERVER'" in text
    assert "'/AGTSVCSTARTUPTYPE=Automatic'" in text
    assert "'/SECURITYMODE=SQL'" in text
    assert "/quiet /norestart /IAcceptLicenseTerms /Edition=Dev" in text
    assert text.count("Get-FileHash -Algorithm SHA256") == 2
    assert "Get-AuthenticodeSignature" in text
    for name in ("SQL_ISO", "SSRS"):
        assert re.fullmatch(r"[0-9A-F]{64}", job["env"][f"{name}_SHA256"])
        assert job["env"][f"{name}_URL"].startswith("https://download.microsoft.com/")
    assert re.fullmatch(r"[0-9A-F]{64}", job["env"]["SSRS_SHA256_ALT"])
    assert job["env"]["SSRS_SHA256_ALT"] != job["env"]["SSRS_SHA256"]
    assert "-notin @($env:SSRS_SHA256, $env:SSRS_SHA256_ALT)" in text
    assert "-notlike 'CN=Microsoft Corporation,*'" in text
    assert "Latin1_General_100_CI_AS_SC_UTF8" in text


def test_database_files_avoid_disks_with_large_sectors(job):
    # Some runners report 8 KB sectors on C:, which SQL Server cannot use (error 5178).
    names = [step.get("name", "") for step in job["steps"]]
    texts = [step.get("run", "").replace("\\", "/") for step in job["steps"]]
    choose = next(i for i, text in enumerate(texts) if "scripts/Select-SqlDataVolume.ps1" in text)
    setup = next(i for i, text in enumerate(texts) if "/ACTION=Install" in text)
    files = next(i for i, text in enumerate(texts) if "sys.master_files" in text)
    assert choose < setup < files, names
    assert "-EnvFile $env:GITHUB_ENV" in texts[choose]
    assert 'if ($env:SQL_DATA_ROOT) { $arguments += "/INSTALLSQLDATADIR=' in texts[setup]
    assert "InstanceDefaultDataPath" in texts[files] and "InstanceDefaultLogPath" in texts[files]
    assert "master_files.txt" in texts[files] and "throw" in texts[files]
    logs = next(step["run"] for step in job["steps"] if step.get("if") == "failure()")
    assert "$env:SQL_DATA_ROOT" in logs

    script = (ROOT / "scripts/Select-SqlDataVolume.ps1").read_text(encoding="utf-8")
    assert "fsutil fsinfo sectorinfo" in script
    assert "PhysicalBytesPerSectorForAtomicity" in script
    assert "$Supported = 4096" in script
    assert "Get-SectorSize 'C:'" in script and "Test-Path -LiteralPath 'D:/'" in script.replace(
        "\\", "/"
    )
    assert "foreach ($extension in 'vhdx', 'vhd')" in script
    for command in ("create vdisk", "attach vdisk", "format fs=ntfs quick", "assign letter"):
        assert command in script, command
    # The new volume is measured, not trusted, and the job stops if no format fits.
    assert 'Sector = (Get-SectorSize "${letter}:")' in script
    assert "SQL Server cannot keep its files on this runner" in script
    assert "SQL_DATA_ROOT=$root" in script


def test_server_steps_run_after_a_successful_publish(job):
    names = {step.get("id"): step for step in job["steps"]}
    assert "if" not in names["publish"]
    for step_id in ("render", "cascade", "mail"):
        assert names[step_id]["if"] == "always() && steps.publish.outcome == 'success'"


def test_evidence_is_uploaded_whatever_the_outcome(job):
    upload = job["steps"][-1]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["if"] == "always()"
    assert upload["with"]["retention-days"] == 30
    paths = upload["with"]["path"].split()
    assert "reports/ssrs/" in paths and "reports/reference.json" in paths
    summary = job["steps"][-2]
    assert summary["if"] == "always()" and "GITHUB_STEP_SUMMARY" in summary["run"]
    assert "not reached" in summary["run"]
    logs = [step for step in job["steps"] if step.get("if") == "failure()"]
    assert len(logs) == 1
    for needed in ("SETUP_LOG", "SSRS*.log", "RS_LOG", "ERRORLOG", "SQLAGENT.OUT"):
        assert needed in logs[0]["run"], needed
    assert "Setup Bootstrap/Log" in job["env"]["SETUP_LOG"].replace("\\", "/")
    assert job["env"]["RS_LOG"].replace("\\", "/").endswith("SSRS/LogFiles")


def test_local_ci_does_not_pick_this_workflow(workflow):
    # The local CI selects workflows by push and pull_request; the Windows job must stay out of it.
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert "push" in ci.get("on", ci.get(True))
    assert set(workflow.get("on", workflow.get(True))).isdisjoint({"push", "pull_request"})


def test_evidence_files_are_written_as_utf8():
    # Tee-Object -FilePath and Out-File write UTF-16 in Windows PowerShell 5.1.
    texts = [WORKFLOW.read_text(encoding="utf-8")] + [
        path.read_text(encoding="utf-8") for path in (ROOT / "scripts").glob("*.ps1")
    ]
    for text in texts:
        assert "Tee-Object -FilePath" not in text and "Out-File" not in text


def test_proxy_objects_are_read_and_written_by_reflection():
    # Proxy members are read and set by reflection, and their declared types are logged.
    proxy = (ROOT / "scripts/RsProxy.ps1").read_text(encoding="utf-8")
    for needed in ("function Get-RsMember", "function Set-RsMember", "function Get-RsShape"):
        assert needed in proxy, needed
    assert proxy.count("GetProperty($Name, [Type[]]@())") == 2
    assert "[Enum]::Parse($target, $Value)" in proxy

    publish = (ROOT / "scripts/Publish-Report.ps1").read_text(encoding="utf-8")
    assert not re.search(r"\.Item\b", publish), "DataSource.Item through the adapter"
    assert "Set-RsMember $source 'Item' $sourceDefinition" in publish
    assert "New-RsObject $proxy 'DataSourceDefinition'" in publish
    for name in (
        "Extension = $provider",
        "ConnectString = $connect",
        "CredentialRetrieval = 'Store'",
        "UserName = $ReaderLogin",
        "Password = $readerPassword",
        "WindowsCredentials = $false",
        "ImpersonateUserSpecified = $true",
    ):
        assert name in publish, name
    assert "local-name()='ConnectString'" in publish and "local-name()='DataProvider'" in publish
    assert "dbo.DataSource AS d JOIN dbo.Catalog AS c" in publish
    assert publish.index("$proxy.SetItemDataSources(") < publish.index(
        "$proxy.TestConnectForItemDataSource("
    )

    parameters = (ROOT / "scripts/Get-ReportParameters.ps1").read_text(encoding="utf-8")
    assert "Get-RsMember $parameter 'ValidValues'" in parameters
    assert not re.search(r"\$parameter\.\w", parameters)
    assert "GetUnresolvedProviderPathFromPSPath($OutFile)" in parameters


def test_publish_takes_the_connection_from_the_unchanged_rdl():
    rdl = ET.parse(ROOT / "rdl/SalesStock.rdl").getroot()
    sources = [e for e in rdl.iter() if e.tag.endswith("}DataSource")]
    assert [s.get("Name") for s in sources] == ["Retail"]
    values = {e.tag.split("}")[1]: e.text for e in sources[0].iter()}
    assert values["DataProvider"] == "SQL"
    assert values["ConnectString"] == "Data Source=localhost;Initial Catalog=RetailLab"


def assigned_parameters(text):
    """Script parameters that the script body assigns to (names compared case-insensitively)."""
    block = re.search(r"(?ms)^param\((.*?)^\)", text)
    if not block:
        return []
    names = re.findall(r"\$(\w+)\s*(?:=|,|$)", block.group(1), flags=re.M)
    body = text[block.end() :]
    return [
        name
        for name in names
        if re.search(rf"(?im)(^\s*\${name}\s*=[^=]|foreach\s*\(\s*\${name}\s+in\b)", body)
    ]


def test_scripts_do_not_assign_to_their_parameters():
    # PowerShell variable names are case-insensitive, and a parameter declared [string] converts
    # whatever is assigned to it: $definition = <object> next to [string]$Definition stored the
    # object as a String on the runner. No script variable may reuse a parameter name.
    for path in sorted((ROOT / "scripts").glob("*.ps1")):
        assert assigned_parameters(path.read_text(encoding="utf-8")) == [], path.name
    broken = "param(\n    [string]$Definition = 'a.rdl',\n    [string]$Name\n)\n$definition = 1\n"
    assert assigned_parameters(broken) == ["Definition"]
    looped = "param(\n    [string]$Name = 'x'\n)\nforeach ($name in 1, 2) { }\n"
    assert assigned_parameters(looped) == ["Name"]


# Positions of the System.String parameters of the ReportService2010 methods the scripts call.
STRING_ARGUMENTS = {
    "GetItemParameters": (0, 1),  # ItemPath, HistoryID
    "CreateFolder": (0, 1),  # Folder, Parent
    "CreateCatalogItem": (0, 1, 2),  # ItemType, Name, Parent
    "GetItemDataSources": (0,),
    "SetItemDataSources": (0,),
    "TestConnectForItemDataSource": (0, 1, 2, 3),  # ItemPath, DataSourceName, UserName, Password
    "GetPolicies": (0,),
    "SetPolicies": (0,),
    "ListChildren": (0,),
}


def proxy_calls(text):
    """(method, top-level arguments) of every $proxy.Method(...) call."""
    calls = []
    for match in re.finditer(r"\$proxy\.(\w+)\(", text):
        depth, start, arguments = 1, match.end(), []
        for i in range(match.end(), len(text)):
            char = text[i]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            if depth == 0 or (depth == 1 and char == ","):
                arguments.append(text[start:i].strip())
                start = i + 1
            if depth == 0:
                break
        calls.append((match.group(1), arguments))
    return calls


def test_proxy_string_arguments_are_never_a_powershell_null():
    # PowerShell turns $null into '' for a .NET string argument: GetItemParameters then read
    # HistoryID '' as a snapshot date (rsParameterTypeMismatch 'snapshotID') on the runner.
    calls = []
    for path in sorted((ROOT / "scripts").glob("*.ps1")):
        calls += proxy_calls(path.read_text(encoding="utf-8"))
    assert {name for name, _ in calls} <= set(STRING_ARGUMENTS), calls
    for name, arguments in calls:
        for position in STRING_ARGUMENTS[name]:
            assert arguments[position].lower() != "$null", (name, arguments)
    history = [arguments for name, arguments in calls if name == "GetItemParameters"]
    assert history and all(arguments[1] == "[NullString]::Value" for arguments in history)
    broken = "$proxy.GetItemParameters($Report, $null, $true, (Make $proxy 'A' @($v)), $null)"
    assert proxy_calls(broken)[0][1][1] == "$null"
    assert len(proxy_calls(broken)[0][1]) == 5


def test_catalog_queries_turn_quoted_identifier_on():
    # sqlcmd starts with QUOTED_IDENTIFIER OFF; ReportServer views and msdb indexes need it ON
    # (Msg 1934 on dbo.ExecutionLog3 on the runner).
    texts = [WORKFLOW.read_text(encoding="utf-8")] + [
        path.read_text(encoding="utf-8") for path in (ROOT / "scripts").glob("*.ps1")
    ]
    lines = [
        line
        for text in texts
        for line in text.splitlines()
        if re.search(r"\bsqlcmd -S\b", line) and re.search(r"ReportServer|msdb", line)
    ]
    assert len(lines) >= 7
    for line in lines:
        assert re.search(r"\bsqlcmd -S \S+ -E -C -I\b", line), line
