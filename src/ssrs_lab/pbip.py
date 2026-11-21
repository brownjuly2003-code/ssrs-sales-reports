"""Build a paginated-report comparison in the portfolio's PBIP file format."""

import json
import uuid
from pathlib import Path

NAME = "Retail"
DEFAULT_DATA_FOLDER = "C:/ssrs-sales-reports/data/"
NAMESPACE = uuid.UUID("bd3d2fbe-af73-4fc6-a4f6-e373e0717a79")
KINDS = {
    "id": ("Int64.Type", "int64", "none", "0"),
    "int": ("Int64.Type", "int64", "none", "0"),
    "date": ("type date", "dateTime", "none", "yyyy-mm-dd"),
    "text": ("type text", "string", "none", None),
}
TABLES = {
    "Calendar": ("Calendar.csv", [("Day", "date")], "Даты отчётного периода"),
    "Store": (
        "DimStore.csv",
        [("StoreId", "id"), ("RegionId", "id"), ("StoreName", "text"), ("RegionName", "text")],
        "Магазины с регионом",
    ),
    "Product": (
        "DimProduct.csv",
        [
            ("ProductId", "id"),
            ("CategoryId", "id"),
            ("ProductName", "text"),
            ("CategoryName", "text"),
        ],
        "Товары с категорией",
    ),
    "Sales": (
        "Sales.csv",
        [
            ("Day", "date"),
            ("StoreId", "id"),
            ("ProductId", "id"),
            ("Units", "int"),
            ("RevenueCents", "int"),
        ],
        "Продажи по дням",
    ),
    "Stock": (
        "Stock.csv",
        [("Day", "date"), ("StoreId", "id"), ("ProductId", "id"), ("OnHand", "int")],
        "Снимок на конец дня",
    ),
}
# A product with stock but no sales shows 0 sold and 0 revenue: BLANK() + 0 is 0 in DAX. The
# measures used COALESCE(SUM(...), 0) before 21.11.2026; the Excel Data Model engine that
# evaluates them without Power BI Desktop rejects COALESCE, and both forms give the same values.
MEASURES = {
    "Sales": [
        (
            "Revenue",
            "IF(NOT ISBLANK([Closing Stock]) || NOT ISBLANK(SUM(Sales[RevenueCents])), DIVIDE(SUM(Sales[RevenueCents]) + 0, 100))",
            "#,0.00",
            "Выручка в рублях",
        ),
        (
            "Units Sold",
            "IF(NOT ISBLANK([Closing Stock]) || NOT ISBLANK(SUM(Sales[Units])), SUM(Sales[Units]) + 0)",
            "#,0",
            "Количество за период",
        ),
        (
            "Average Price",
            "DIVIDE([Revenue], [Units Sold])",
            "#,0.00",
            "Средневзвешенная цена в рублях",
        ),
    ],
    "Stock": [
        (
            "Closing Stock",
            "CALCULATE(SUM(Stock[OnHand]), LASTDATE('Calendar'[Day]))",
            "#,0",
            "Остаток на последний выбранный день",
        )
    ],
}
RELATIONSHIPS = [
    (f"{fact}.{key}", f"{dim}.{key}")
    for fact in ("Stock", "Sales")
    for dim, key in (("Store", "StoreId"), ("Product", "ProductId"), ("Calendar", "Day"))
]


def col(table, field):
    return ("Column", table, field)


def mea(table, field):
    return ("Measure", table, field)


metrics = [
    mea("Sales", "Revenue"),
    mea("Sales", "Units Sold"),
    mea("Sales", "Average Price"),
    mea("Stock", "Closing Stock"),
]
slicers = [
    ("Calendar", "Day", "Период"),
    ("Store", "RegionName", "Регион"),
    ("Store", "StoreName", "Магазин"),
    ("Product", "CategoryName", "Категория"),
]
PAGES = [
    (
        "retailsummary",
        "Продажи и остатки",
        [
            *(
                ("slicer", i * 310, 0, 300, 145, {"Values": [col(t, f)]}, title)
                for i, (t, f, title) in enumerate(slicers)
            ),
            (
                "pivotTable",
                0,
                160,
                1240,
                535,
                {
                    "Rows": [
                        col("Store", "RegionName"),
                        col("Store", "StoreName"),
                        col("Product", "CategoryName"),
                        col("Product", "ProductName"),
                    ],
                    "Values": metrics,
                },
                "Продажи за период и остаток на последнюю дату",
            ),
        ],
    )
]

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"


def guid(*parts):
    return str(uuid.uuid5(NAMESPACE, "/".join(parts)))


def quote(name):
    return f"'{name}'" if not name.isidentifier() else name


def _dump(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _table_tmdl(table):
    file_name, columns, description = TABLES[table]
    lines = [f"/// {description}", f"table {table}", f"\tlineageTag: {guid('table', table)}", ""]
    for name, measure_dax, fmt, note in MEASURES.get(table, []):
        lines += [
            f"\t/// {note}",
            f"\tmeasure {quote(name)} = {measure_dax}",
            f"\t\tformatString: {fmt}",
            f"\t\tlineageTag: {guid('measure', table, name)}",
            "",
        ]
    for name, kind in columns:
        _, data_type, summarize, fmt = KINDS[kind]
        lines += [f"\tcolumn {quote(name)}", f"\t\tdataType: {data_type}"]
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        lines += [
            f"\t\tlineageTag: {guid('column', table, name)}",
            f"\t\tsummarizeBy: {summarize}",
            f"\t\tsourceColumn: {name}",
            "",
            "\t\tannotation SummarizationSetBy = Automatic",
            "",
        ]
    types = ", ".join(f'{{"{name}", {KINDS[kind][0]}}}' for name, kind in columns)
    source = [
        "let",
        f'    Source = Csv.Document(File.Contents(DataFolder & "{file_name}"),'
        f' [Delimiter = ",", Columns = {len(columns)}, Encoding = 65001,'
        " QuoteStyle = QuoteStyle.Csv]),",
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        f'    Typed = Table.TransformColumnTypes(Promoted, {{{types}}}, "en-US")',
        "in",
        "    Typed",
    ]
    lines += [f"\tpartition {table} = m", "\t\tmode: import", "\t\tsource ="]
    lines += [f"\t\t\t\t{line}" for line in source]
    lines += ["", "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(lines)


def semantic_model_files():
    files = {
        ".platform": _dump(
            {
                "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
                "metadata": {"type": "SemanticModel", "displayName": NAME},
                "config": {"version": "2.0", "logicalId": guid("semanticModel")},
            }
        ),
        "definition.pbism": _dump(
            {
                "$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
                "version": "4.0",
                "settings": {},
            }
        ),
        "definition/database.tmdl": "database\n\tcompatibilityLevel: 1567\n",
        "definition/model.tmdl": "\n".join(
            [
                "model Model",
                "\tculture: ru-RU",
                "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
                "\tsourceQueryCulture: ru-RU",
                "\tdataAccessOptions",
                "\t\tlegacyRedirects",
                "\t\treturnErrorValuesAsNull",
                "",
                "	annotation PBI_QueryOrder = "
                + json.dumps(["DataFolder", *TABLES], ensure_ascii=False).replace(" ", ""),
                "",
                *[f"ref table {table}" for table in TABLES],
                "",
            ]
        ),
        "definition/expressions.tmdl": "\n".join(
            [
                "/// Folder with the CSV files (end with a backslash). Change it after cloning.",
                f'expression DataFolder = "{DEFAULT_DATA_FOLDER}" meta [IsParameterQuery = true,'
                ' Type = "Text", IsParameterQueryRequired = true]',
                f"\tlineageTag: {guid('expression', 'DataFolder')}",
                "",
                "\tannotation PBI_ResultType = Text",
                "",
            ]
        ),
    }
    rel = []
    for source, target in RELATIONSHIPS:
        rel += [
            f"relationship {guid('relationship', source, target)}",
            f"\tfromColumn: {source}",
            f"\ttoColumn: {target}",
            "",
        ]
    files["definition/relationships.tmdl"] = "\n".join(rel)
    for table in TABLES:
        files[f"definition/tables/{table}.tmdl"] = _table_tmdl(table)
    return files


def _field(field):
    kind, table, name = field
    return {kind: {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def _visual(page, index, spec):
    visual_type, x, y, width, height, roles, title = spec
    name = guid("visual", page, str(index)).replace("-", "")[:20]
    query = {
        role: {
            "projections": [
                {
                    "field": _field(field),
                    "queryRef": f"{field[1]}.{field[2]}",
                    "nativeQueryRef": field[2],
                }
                for field in fields
            ]
        }
        for role, fields in roles.items()
    }
    visual = {"visualType": visual_type, "query": {"queryState": query}}
    if title:
        visual["visualContainerObjects"] = {
            "title": [
                {
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "text": {"expr": {"Literal": {"Value": f"'{title}'"}}},
                    }
                }
            ]
        }
    visual["drillFilterOtherVisuals"] = True
    return name, {
        "$schema": f"{SCHEMA}/item/report/definition/visualContainer/1.3.0/schema.json",
        "name": name,
        "position": {
            "x": x,
            "y": y,
            "z": index * 1000,
            "width": width,
            "height": height,
            "tabOrder": index * 1000,
        },
        "visual": visual,
    }


def report_files():
    files = {
        ".platform": _dump(
            {
                "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
                "metadata": {"type": "Report", "displayName": NAME},
                "config": {"version": "2.0", "logicalId": guid("report")},
            }
        ),
        "definition.pbir": _dump(
            {
                "$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
                "version": "4.0",
                "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}},
            }
        ),
        "definition/version.json": _dump(
            {
                "$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json",
                "version": "2.0.0",
            }
        ),
        "definition/report.json": _dump(
            {
                "$schema": f"{SCHEMA}/item/report/definition/report/1.2.0/schema.json",
                "themeCollection": {
                    "baseTheme": {
                        "name": "CY24SU10",
                        "reportVersionAtImport": "5.61",
                        "type": "SharedResources",
                    }
                },
                "layoutOptimization": "None",
                "resourcePackages": [
                    {
                        "name": "SharedResources",
                        "type": "SharedResources",
                        "items": [
                            {
                                "name": "CY24SU10",
                                "path": "BaseThemes/CY24SU10.json",
                                "type": "BaseTheme",
                            }
                        ],
                    }
                ],
                "settings": {
                    "useStylableVisualContainerHeader": True,
                    "defaultDrillFilterOtherVisuals": True,
                },
            }
        ),
        "StaticResources/SharedResources/BaseThemes/CY24SU10.json": _dump(
            {
                "name": "CY24SU10",
                "dataColors": ["#2F5D8A", "#8AA9C8", "#C0504D", "#7F7F7F", "#4F8F5B", "#D9A441"],
                "background": "#FFFFFF",
                "foreground": "#252423",
                "tableAccent": "#2F5D8A",
            }
        ),
        "definition/pages/pages.json": _dump(
            {
                "$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
                "pageOrder": [page for page, _, _ in PAGES],
                "activePageName": PAGES[0][0],
            }
        ),
    }
    for page, display, visuals in PAGES:
        files[f"definition/pages/{page}/page.json"] = _dump(
            {
                "$schema": f"{SCHEMA}/item/report/definition/page/1.0.0/schema.json",
                "name": page,
                "displayName": display,
                "displayOption": "FitToPage",
                "height": 720,
                "width": 1280,
            }
        )
        for index, spec in enumerate(visuals):
            name, body = _visual(page, index, spec)
            files[f"definition/pages/{page}/visuals/{name}/visual.json"] = _dump(body)
    return files


def project_files():
    files = {
        f"{NAME}.pbip": _dump(
            {
                "$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json",
                "version": "1.0",
                "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                "settings": {"enableAutoRecovery": True},
            }
        ),
        ".gitignore": "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n",
    }
    for path, text in semantic_model_files().items():
        files[f"{NAME}.SemanticModel/{path}"] = text
    for path, text in report_files().items():
        files[f"{NAME}.Report/{path}"] = text
    return files


def write_project(target):
    for path, body in project_files().items():
        file = Path(target) / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(body.encode("utf-8"))
